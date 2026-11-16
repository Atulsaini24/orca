"""Correctness tests for routing, dispatch invariants, matching, data loading and the API."""
import itertools
import json
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from orca import city as G
from orca import cityspec
from orca import config as C
from orca.city import GridCity, is_arterial
from orca.dispatch import DROP, IDLE, OFFERED, PICK, TRIP, OrcaDispatcher
from orca.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
CITIES = ["grid", *cityspec.built()]                          # every built real city, plus the grid


class _Car:
    def __init__(self, x, z, id_=0):
        self.x, self.z, self.id, self.route, self.ri, self.node, self.prev_node = x, z, id_, [], 0, -1, -1


# ---------------------------------------------------------------------------- routing
def test_grid_routes_stay_on_roads_and_reach_target():
    city, rng = GridCity(), random.Random(0)
    for _ in range(2000):
        x, z = city.random_point(rng)
        tx, tz = city.snap(rng.random() * city.WX, rng.random() * city.WZ)
        pts = city.route(_Car(x, z, rng.randrange(1000)), tx, tz)
        px, pz = x, z
        for wx, wz, mult, _node in pts:
            assert (wx == px) or (wz == pz), "every leg moves along one axis"
            assert city.on_road(wx, wz) and mult >= 1.0
            px, pz = wx, wz
        assert (px, pz) == (tx, tz)


def test_arterials():
    assert is_arterial(0) and is_arterial(7) and is_arterial(C.GRID - 1)
    assert not is_arterial(3) and not is_arterial(3.5)


@pytest.mark.skipif("manhattan" not in CITIES, reason="street network not built (scripts/build_streets.py)")
def test_street_routes_follow_one_way_streets_to_the_target():
    city, rng = G.use("manhattan"), random.Random(1)
    assert city.n > 4000 and city.N_ZONES == 63
    for _ in range(300):
        car = _Car(*city.random_point(rng))
        car.node = city.nearest_node(car.x, car.z)
        tx, tz = city.random_point(rng)
        pts = city.route(car, tx, tz)
        nodes = [car.node] + [int(n) for *_, n in pts if n >= 0]
        for u, v in itertools.pairwise(nodes):
            assert (u, v) in city.edge_of, "every hop is a directed street segment (one-way rules respected)"
        assert nodes[-1] == city.nearest_node(tx, tz)
        # the route is the fastest one: its free-flow cost equals the shortest-path distance
        cost = sum(city.length[city.edge_of[(u, v)]] / city.edge_mult[city.edge_of[(u, v)]] for u, v in itertools.pairwise(nodes))
        assert cost == pytest.approx(city.tree_to(nodes[-1])[0][nodes[0]], rel=1e-5)     # trees are float32


@pytest.mark.skipif("manhattan" not in CITIES, reason="street network not built")
@pytest.mark.skipif("chicago" not in CITIES, reason="Chicago not built")
def test_chicago_landmarks_land_in_the_right_neighbourhoods():
    from orca.demand import StreetProjector
    from orca.geo import to_il_east
    city = G.use("chicago")
    proj = StreetProjector(city)
    for lon, lat, zone in [(-87.635915, 41.878876, "Loop"), (-87.6051, 41.8917, "Streeterville"), (-87.6553, 41.9484, "Wrigleyville"),
                           (-87.6334, 41.9211, "Lincoln Park")]:
        x, z = proj(*to_il_east(lon, lat))
        assert city.zone_names[city.zone_of(float(x), float(z))] == zone


@pytest.mark.parametrize("name", [c for c in CITIES if c != "grid"])
def test_riders_are_never_picked_up_on_an_expressway(name):
    city, rng = G.use(name), random.Random(5)
    motor = {i for i, n in enumerate(["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                                      "living_street", "motorway_link", "trunk_link"]) if n in ("motorway", "motorway_link", "trunk_link")}
    touching: dict[int, list[int]] = {}
    for e, (u, v) in enumerate(zip(city.src.tolist(), city.dst.tolist())):
        touching.setdefault(u, []).append(e)
        touching.setdefault(v, []).append(e)
    for _ in range(500):
        x, z = city.snap(rng.random() * city.WX, rng.random() * city.WZ)
        k = city.nearest_node(x, z)
        assert any(int(city.cls[e]) not in motor for e in touching[k]), "pickup snapped to an expressway-only intersection"


def test_give_up_guard_prefers_a_car_that_arrives_in_time(monkeypatch):
    from orca import config
    from orca.dispatch import deadline_cost
    monkeypatch.setattr(config, "DEADLINE_W", 1.0)                     # each city tunes its own weight; test the shape
    assert deadline_cost(60, 120) < 1                                    # fresh request: no penalty
    late, early = deadline_cost(700, 240), deadline_cost(700, 60)        # waited 11:40: a 4-min car arrives too late
    assert late > 400 and early < 60


def test_real_landmarks_land_in_the_right_taxi_zones():
    from orca.demand import StreetProjector
    from orca.geo import to_state_plane
    city = G.use("manhattan")
    proj = StreetProjector(city)
    for lon, lat, zone in [(-73.985664, 40.748440, "Midtown South"), (-73.9654, 40.7829, "Central Park"),
                           (-74.0134, 40.7127, "World Trade Center"), (-73.9442, 40.8116, "Central Harlem North")]:
        x, z = proj(*to_state_plane(lon, lat))
        assert city.zone_names[city.zone_of(float(x), float(z))] == zone


@pytest.mark.skipif("manhattan" not in CITIES, reason="street network not built")
def test_street_cruising_and_eta_tree():
    city, rng = G.use("manhattan"), random.Random(2)
    car = _Car(*city.random_point(rng))
    pts = city.cruise(car, rng)
    assert len([n for *_, n in pts if n >= 0]) == 3                    # three blocks of random turns
    cars = [(*city.random_point(rng), city.nearest_node(*city.random_point(rng))) for _ in range(20)]
    eta = city.eta_many(cars, *city.random_point(rng))
    assert np.isfinite(eta).all() and (eta >= 0).all()


@pytest.mark.parametrize("name", [c for c in CITIES if c != "grid"])
def test_routes_follow_the_hour_and_go_around_accidents(name):
    from orca.routing_report import route_time
    city, rng = G.use(name), random.Random(3)
    try:
        city.set_conditions(17 * 3600 + 60.0, [])
        assert city.traffic_routing
        w17 = city._w_free / city.zone_hour[city.edge_zone, 17]
        for _ in range(100):
            car = _Car(*city.random_point(rng))
            car.node = city.nearest_node(car.x, car.z)
            tx, tz = city.random_point(rng)
            nodes = [car.node] + [int(n) for *_, n in city.route(car, tx, tz) if n >= 0]
            for u, v in itertools.pairwise(nodes):
                assert (u, v) in city.edge_of
            cost = sum(w17[city.edge_of[(u, v)]] for u, v in itertools.pairwise(nodes))
            assert cost == pytest.approx(city.tree_to(nodes[-1])[0][nodes[0]], rel=1e-4)    # fastest at 17:00
        # an accident on a route's middle: the new route is never slower under the accident, and usually avoids it
        avoided = 0
        for _ in range(30):
            u, v = city.nearest_node(*city.random_point(rng)), city.nearest_node(*city.random_point(rng))
            city.set_conditions(17 * 3600 + 60.0, [])
            path = city.path_nodes(u, v)
            if len(path) < 8:
                continue
            mid = path[len(path) // 2]
            acc = (float(city.nx[mid]), float(city.nz[mid]), 20.0, 0.78)
            d2 = (city.edge_mx - acc[0]) ** 2 + (city.edge_mz - acc[1]) ** 2
            w_acc = w17 / np.maximum(0.22, 1 - 0.78 * np.exp(-d2 / 800.0))
            blind, _ = route_time(city, u, v, city.tree_to(v)[1], w_acc)
            city.set_conditions(17 * 3600 + 60.0, [acc])
            aware, _ = route_time(city, u, v, city.tree_to(v)[1], w_acc)
            assert aware <= blind * (1 + 1e-5)
            avoided += aware < blind * 0.999
        assert avoided >= 5
    finally:
        city.set_conditions(-1.0, [])


# ---------------------------------------------------------------------------- engine invariants
@pytest.fixture(scope="module", params=CITIES)
def engine(request):
    e = Engine(seed=5, fleet=500, use_learned=False, city=request.param)
    for i in range(1200):
        e.step(1.0)
        if i == 400:
            e.world.trigger_spike()
            e.world.trigger_outage()
    return e


def check_plans(d):
    """Every request is held by at most one car; each car's plan, load and state agree."""
    f, seen = d.f, {}
    for i in range(d.n):
        held = {s.rs.r.id for s in f.stops[i]} | ({f.offer_rs[i].r.id} if f.offer_rs[i] is not None else set())
        for rid in held:
            assert rid not in seen, f"request {rid} held by cars {seen[rid]} and {i}"
            seen[rid] = i
        for s in f.stops[i]:
            assert s.rs.agent == i
            if s.kind == PICK:
                assert any(t.kind == DROP and t.rs is s.rs for t in f.stops[i]), "every pickup has its drop-off"
        if f.offer_rs[i] is not None:
            assert f.state[i] == OFFERED and f.offer_rs[i].agent == i
        if f.state[i] == IDLE:
            assert not f.stops[i] and f.offer_rs[i] is None
        onboard = sum(1 for s in f.stops[i] if s.kind == DROP and s.rs.status == "ontrip")
        assert f.load[i] == onboard <= (C.POOL_CAP if d.pool else 1)
        picks = [k for k, s in enumerate(f.stops[i]) if s.kind == PICK]
        drops = {s.rs.r.id: k for k, s in enumerate(f.stops[i]) if s.kind == DROP}
        for k in picks:
            assert k < drops[f.stops[i][k].rs.r.id], "a rider is picked up before being dropped off"


@pytest.mark.parametrize("which", ["smart", "base"])
def test_no_double_assignment(engine, which):
    check_plans(engine.smart if which == "smart" else engine.base)


def test_both_policies_see_identical_demand(engine):
    a, b = engine.smart, engine.base
    total = engine.world.next_req - 1
    for d in (a, b):
        in_flight = int(np.count_nonzero(d.f.state == TRIP))
        assert d.m["served"] + d.m["lost"] + len(d.active) <= total
        assert d.m["served"] >= in_flight


def test_orca_beats_nearest_on_mean_wait(engine):
    # One 20-minute run is noisy (a single seed can go either way by a few seconds), so average three seeds.
    smart, base = [engine.smart.stats()["mean"]], [engine.base.stats()["mean"]]
    for seed in (6, 7):
        e = Engine(seed=seed, fleet=500, use_learned=False, city=engine.city.name)
        for i in range(1200):
            e.step(1.0)
            if i == 400:
                e.world.trigger_spike()
                e.world.trigger_outage()
        smart.append(e.smart.stats()["mean"])
        base.append(e.base.stats()["mean"])
    assert np.mean(smart) < np.mean(base)


def test_snapshots_are_json(engine):
    f = json.loads(json.dumps(engine.frame("smart")))
    s = json.loads(json.dumps(engine.summary(), allow_nan=False))
    assert len(f["agents"]["x"]) == 500
    assert set(s["stats"]) == {"smart", "base"}
    assert "orca_wait_seconds" in engine.prometheus()


def test_commands(engine):
    assert engine.command({"cmd": "speed", "value": 999})["ok"] and engine.speed == 120
    assert engine.command({"cmd": "demand", "value": 1.3})["ok"] and engine.world.demand_mult == 1.3
    assert not engine.command({"cmd": "launch-rockets"})["ok"]


def test_ledger_matches_dispatchers(engine):
    a = engine.ledger.analytics()
    for key, d in (("smart", engine.smart), ("base", engine.base)):
        assert a[key]["trips"] == d.m["served"]
        assert a[key]["lost"] == d.m["lost"]
        assert sum(d.z_n) == d.m["served"] and sum(d.hist) == d.m["served"]
        if a[key]["trips"]:
            assert a[key]["p50"] <= a[key]["p95"]
    assert len(engine.ledger.recent(10)) == 10


def test_timelines_are_ordered(engine):
    order = {"request": 0, "offer": 1, "decline": 2, "expire": 2, "accept": 3, "pickup": 4, "cancel": 4}
    checked = 0
    for rid in list(engine.smart.timelines)[-300:]:
        tl = engine.smart.timeline(rid)
        assert tl[0]["k"] == "request"
        ts = [e["t"] for e in tl]
        assert ts == sorted(ts), "events happen in time order"
        kinds = [e["k"] for e in tl]
        if "pickup" in kinds:
            assert kinds[-1] == "pickup" and kinds[-2] == "accept"
            assert kinds.count("accept") == 1
            checked += 1
        for k in kinds:
            assert k in order
    assert checked > (50 if engine.city.name == "grid" else 20)   # real-street trips are longer: fewer finish in 20 min
    s = engine.summary()
    if s["notable"]:
        assert s["notable"]["timeline"][0]["k"] == "request"
    assert len(s["zones"]["smart"]) == engine.city.N_ZONES and len(s["hist"]["base"]) == 12


# ---------------------------------------------------------------------------- matching
def test_dense_and_sparse_assignment_agree(monkeypatch):
    rng = np.random.default_rng(1)
    d = OrcaDispatcher.__new__(OrcaDispatcher)
    for _trial in range(50):
        nq, nu = int(rng.integers(2, 12)), int(rng.integers(2, 12))
        qi, ci = [], []
        for q in range(nq):
            for j in rng.choice(nu, size=min(nu, 4), replace=False):
                qi.append(q)
                ci.append(int(j))
        qi, ci = np.array(qi), np.array(ci)
        adj = rng.uniform(5, 200, len(qi))
        monkeypatch.setattr(C, "HUNGARIAN_MAX", 10_000)
        dense = d.solve(qi, ci, adj, nq)
        monkeypatch.setattr(C, "HUNGARIAN_MAX", -1)
        sparse = d.solve(qi, ci, adj, nq)
        # both are exact: as many requests served, at the same total cost
        assert len(dense) == len(sparse)
        assert adj[dense].sum() == pytest.approx(adj[sparse].sum())
        for m in (dense, sparse):
            assert len(set(ci[m])) == len(m) and len(set(qi[m])) == len(m)
        # and no greedy pass beats them
        taken, done, greedy = set(), set(), []
        for k in np.argsort(adj):
            if qi[k] not in done and ci[k] not in taken:
                taken.add(ci[k])
                done.add(qi[k])
                greedy.append(k)
        assert len(dense) >= len(greedy)
        if len(dense) == len(greedy):
            assert adj[dense].sum() <= adj[greedy].sum() + 1e-9


# ---------------------------------------------------------------------------- shared rides
def _line_times(xs):
    """Driving times between points on a straight road at 1 unit per second."""
    xs = np.asarray(xs, dtype=float)
    return np.abs(xs[:, None] - xs[None, :])


def test_insertion_puts_a_rider_on_the_way_and_respects_limits():
    from orca.kernels import best_insertion
    # car at 0 carrying rider A to 100; new rider B from 20 to 80 lies on the way
    T = _line_times([0, 100, 20, 80])
    cost, i, j, eta = best_insertion(T, 0.0, 0.0, np.array([-1]), np.array([1e9]), 1, 3, 1e9, 1e9, 60.0, 0.5)
    assert (i, j) == (0, 0) and eta == 20.0 and cost == 20.0      # pick B, drop B, then drop A: nobody delayed
    # with one seat, B can only be fetched after A is home
    cost, i, j, eta = best_insertion(T, 0.0, 0.0, np.array([-1]), np.array([1e9]), 1, 1, 1e9, 1e9, 60.0, 0.5)
    assert (i, j) == (1, 1) and eta == 180.0
    # ... unless B would give up first
    assert not np.isfinite(best_insertion(T, 0.0, 0.0, np.array([-1]), np.array([1e9]), 1, 1, 150.0, 1e9, 60.0, 0.5)[0])
    # A must be home by t=90: impossible whatever the order, since even direct takes 100
    assert not np.isfinite(best_insertion(T, 0.0, 0.0, np.array([-1]), np.array([90.0]), 1, 3, 1e9, 1e9, 60.0, 0.5)[0])
    # B going backwards (from 20 to 5): with A's deadline loose, drop A first would delay B too much
    T = _line_times([0, 100, 20, 5])
    cost, i, j, _ = best_insertion(T, 0.0, 0.0, np.array([-1]), np.array([1e9]), 1, 3, 1e9, 40.0, 15.0, 0.5)
    assert (i, j) == (0, 0) and cost == pytest.approx(20 + 0.5 * 30)   # A arrives 30 s later; B rides direct


def test_route_arena_compaction_keeps_every_route():
    from orca.dispatch import Fleet
    f = Fleet([(i, 0.0, 0.0, 0.9, 0.0) for i in range(50)])
    rng, want = np.random.default_rng(0), {}
    for step in range(4000):
        i = int(rng.integers(50))
        m = int(rng.integers(1, 40))
        wp = np.c_[rng.random((m, 3)), np.full(m, float(step))]
        f.set_route(i, wp)
        want[i] = wp
        if step % 7 == 0:                                      # a car drives part of its route
            f.r_pos[i] += min(3, f.r_end[i] - f.r_pos[i])
            want[i] = want[i][min(3, len(want[i])):]
    for i, wp in want.items():
        assert np.array_equal(f.remaining(i), wp)
    assert len(f.arena) >= f.top


@pytest.mark.parametrize("city", CITIES)
def test_shared_rides_keep_their_promises(city):
    e = Engine(seed=4, fleet=300, use_learned=False, city=city, pool=True, demand_fleet=420)
    for _ in range(1500):
        e.step(1.0)
    check_plans(e.smart)                                         # capacity, order of stops, one car per rider
    s = e.smart.stats()
    assert s["shared"] > 0.02, "short of cars, riders who accept sharing do share"
    assert 1.0 <= s["detour"] <= 1 + C.POOL_DETOUR + 0.25        # shared rides stay close to the direct drive
    assert e.base.stats()["shared"] == 0
    f = json.loads(json.dumps(e.frame("smart")))
    assert max(f["agents"]["o"]) <= C.POOL_CAP


# ---------------------------------------------------------------------------- real-data path
@pytest.mark.parametrize("city", CITIES)
def test_tlc_loader_and_replay(tmp_path, city):
    p = tmp_path / "trips.csv"
    rng = random.Random(3)
    rows = ["tpep_pickup_datetime,tpep_dropoff_datetime,pickup_longitude,pickup_latitude,dropoff_longitude,dropoff_latitude"]
    for _i in range(3000):
        h, m = rng.randrange(24), rng.randrange(60)
        plat, plon = 40.72 + rng.random() * 0.07, -73.99 + rng.random() * 0.03
        rows.append(f"2015-01-13 {h:02d}:{m:02d}:00,2015-01-13 {h:02d}:{m:02d}:59,{plon},{plat},{plon + 0.005},{plat + 0.004}")
    rows.append("2015-01-13 10:00:00,2015-01-13 10:10:00,0,0,0,0")              # junk row is filtered
    p.write_text("\n".join(rows))
    e = Engine(seed=1, fleet=300, demand="tlc", data=str(p), use_learned=False, city=city)
    src, c = e.world.source, e.city
    assert src.n == 3000
    assert src.px.min() >= 0 and src.px.max() <= c.WX and src.pz.min() >= 0 and src.pz.max() <= c.WZ
    got = 0
    for _ in range(600):
        e.step(1.0)
    got = e.world.next_req - 1
    assert got > 0


PREPARED = [c for c in cityspec.built() if cityspec.trips_file(c) is not None]


@pytest.mark.skipif(not PREPARED, reason="real trips not prepared (make data)")
@pytest.mark.parametrize("city", PREPARED)
def test_real_trip_replay(city):
    e = Engine(seed=1, fleet=600, demand="tlc", data=str(cityspec.trips_file(city)), use_learned=False, city=city)
    src = e.world.source
    assert src.n > 500_000 and src.days >= 15
    assert "real" in e.demand_label()
    assert e.world.city_traffic and e.city.zone_hour is not None and e.city.zone_hour.shape == (e.city.N_ZONES, 24)
    hourly = np.bincount((src.sec // 3600).astype(int), minlength=24)
    assert hourly[18] > 3 * hourly[4]                                   # real evening peak vs. 4 am lull
    assert e.city.traffic(100, 100, 4 * 3600) > e.city.traffic(100, 100, 18 * 3600)   # night faster than the rush
    for _ in range(300):
        e.step(1.0)
    assert e.world.next_req > 50


def test_prepared_schema_loader(tmp_path):
    import pandas as pd

    from orca.demand import load_trips
    n = 500
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"tpep_pickup_datetime": pd.Timestamp("2015-01-13 08:00") + pd.to_timedelta(rng.integers(0, 3600, n), "s")})
    df["tpep_dropoff_datetime"] = df.tpep_pickup_datetime + pd.Timedelta("10min")
    for c in ("pickup_e", "pickup_n", "dropoff_e", "dropoff_n"):
        df[c] = rng.normal(0, 2000, n).astype("float32")
    df.to_parquet(tmp_path / "t.parquet")
    out = load_trips(tmp_path / "t.parquet")
    assert len(out) == n and np.allclose(out.ex.values, df.pickup_e.values)


def test_zone_centroids_from_shapefile(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import zone_centroids as zc

    square = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]
    content = struct.pack("<i4d2i", 5, 0, 0, 10, 10, 1, len(square)) + struct.pack("<i", 0)
    content += b"".join(struct.pack("<dd", *pt) for pt in square)
    shp = struct.pack(">i", 9994) + b"\0" * 20 + struct.pack(">i", (100 + 8 + len(content)) // 2) + struct.pack("<ii", 1000, 5)
    shp += b"\0" * (100 - len(shp))
    shp += struct.pack(">ii", 1, len(content) // 2) + content
    (tmp_path / "z.shp").write_bytes(shp)
    fields = [(b"LocationID", b"N", 5), (b"borough", b"C", 12)]
    hdr_len = 32 + 32 * len(fields) + 1
    rec_len = 1 + sum(f[2] for f in fields)
    dbf = struct.pack("<BBBBIHH20x", 3, 26, 1, 1, 1, hdr_len, rec_len)
    for name, t, ln in fields:
        dbf += name.ljust(11, b"\0") + t + b"\0" * 4 + bytes([ln]) + b"\0" * 15
    dbf += b"\r" + b" " + b"   42" + b"Manhattan".ljust(12) + b"\x1a"
    (tmp_path / "z.dbf").write_bytes(dbf)
    (pts, parts), = zc.read_shp(tmp_path / "z.shp")
    assert zc.polygon_centroid(pts, parts) == pytest.approx((5.0, 5.0))
    assert zc.read_dbf(tmp_path / "z.dbf")[0] == {"LocationID": "42", "borough": "Manhattan"}


# ---------------------------------------------------------------------------- API
def test_api_end_to_end():
    import requests
    port = 8877
    proc = subprocess.Popen([sys.executable, "-m", "orca.server", "--port", str(port), "--fleet", "300", "--demand", "synthetic",
                             "--city", "grid"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                if requests.get(base + "/api/health", timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        h = requests.get(base + "/api/health").json()
        assert h["mode"] == "live" and h["fleet"] == 300
        assert requests.post(base + "/api/command", json={"cmd": "spike"}).json()["ok"]
        assert requests.post(base + "/api/command", json={"cmd": "nope"}).status_code == 400
        with requests.get(base + "/api/stream?view=base", stream=True, timeout=5) as r:
            events, t0 = [], time.time()
            for line in r.iter_lines(decode_unicode=True):
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if time.time() - t0 > 1.5:
                    break
        assert "summary" in events and events.count("frame") >= 5
        assert "orca_requests_served_total" in requests.get(base + "/metrics").text
        an = requests.get(base + "/api/analytics").json()
        assert set(an) == {"smart", "base"} and len(an["smart"]["zones"]) == 49                  # grid city: 7 x 7 zones
        assert requests.get(base + "/api/analytics?since=abc").status_code == 400
        assert isinstance(requests.get(base + "/api/trips?limit=5").json(), list)
        m = requests.get(base + "/api/models").json()
        assert "in_use" in m and "eta" in m
        assert requests.get(base + "/app").ok and "ORCA" in requests.get(base + "/app").text
        assert requests.get(base + "/", allow_redirects=True).ok                   # home page, or redirect to /app
        assert requests.get(base + "/media/../server.py").status_code == 404
        assert requests.get(base + "/media/secrets.txt").status_code == 404
    finally:
        proc.terminate()
        proc.wait(5)


@pytest.mark.skipif(len(CITIES) < 3, reason="needs two real cities")
def test_worker_processes_run_the_requested_city():
    """Regression: pool workers start on the default city and must switch to the one each run asks for."""
    from orca.runs import RunSpec, run_many
    specs = [RunSpec(seed=1, minutes=1, warm=5, demand="synthetic", city=c) for c in CITIES[1:]]
    res = run_many(specs + specs[::-1], workers=2, progress=False)
    assert [r["spec"]["city"] for r in res] == [s.city for s in specs + specs[::-1]]
