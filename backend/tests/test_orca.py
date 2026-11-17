"""Correctness tests for routing, dispatch invariants, matching, data loading and the API."""
import itertools
import json
import math
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
from orca.dispatch import DROP, IDLE, OFFERED, PICK, PICKUP, TRIP, OrcaDispatcher
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
    if city.zone_hour is None:
        pytest.skip(f"{name} has no real traffic profile (built without trip records)")
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
        held = {s.rs.r.id for s in f.stops[i] if s.rs is not None} | ({f.offer_rs[i].r.id} if f.offer_rs[i] is not None else set())
        for rid in held:
            assert rid not in seen, f"request {rid} held by cars {seen[rid]} and {i}"
            seen[rid] = i
        for s in f.stops[i]:
            if s.rs is None:                                   # a charging stop: the only stop in the plan
                assert len(f.stops[i]) == 1 and d.ev is not None
                continue
            assert s.rs.agent == i
            if s.kind == PICK:
                assert any(t.kind == DROP and t.rs is s.rs for t in f.stops[i]), "every pickup has its drop-off"
        if f.offer_rs[i] is not None:
            assert f.state[i] == OFFERED and f.offer_rs[i].agent == i
        if f.state[i] == IDLE:
            assert not f.stops[i] and f.offer_rs[i] is None
        onboard = sum(1 for s in f.stops[i] if s.kind == DROP and s.rs is not None and s.rs.status == "ontrip")
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


def test_packed_frames_match_json_frames(engine):
    import base64
    f, p = engine.frame("smart"), engine.frame("smart", packed=True)["agents"]
    q, n = p["q"], p["n"]
    u16 = lambda k: np.frombuffer(base64.b64decode(p[k]), dtype="<u2")
    u8 = lambda k: np.frombuffer(base64.b64decode(p[k]), dtype=np.uint8)
    assert n == len(f["agents"]["x"])
    assert np.allclose(u16("x") / q, f["agents"]["x"], atol=0.6 / q + 0.01) and np.allclose(u16("z") / q, f["agents"]["z"], atol=0.6 / q + 0.01)
    tx = u16("tx")
    none = np.array(f["agents"]["tx"]) < 0
    assert ((tx == 65535) == none).all() and np.allclose(tx[~none] / q, np.array(f["agents"]["tx"])[~none], atol=0.6 / q + 0.01)
    assert (u8("s") == f["agents"]["s"]).all() and (u8("h") == f["agents"]["h"]).all()
    assert len(json.dumps(p)) < 0.6 * len(json.dumps(f["agents"]))


def test_incidents_where_clicked_and_drivers_logging_off():
    e = Engine(seed=9, fleet=300, use_learned=False, city="grid")
    e.warm_up(120)
    assert e.command({"cmd": "accident", "x": 40.0, "z": 200.0})["ok"]
    b = e.world.blobs[-1]
    assert b.accident and abs(b.x - 40) <= 10 and abs(b.z - 200) <= 10
    assert e.command({"cmd": "outage", "x": 9999, "z": -5})["ok"]                 # clamped into the city
    o = e.world.outages[-1]
    assert 0 <= o.x <= e.city.WX and 0 <= o.z <= e.city.WZ
    assert not e.command({"cmd": "spike", "x": "left"})["ok"]
    assert e.command({"cmd": "logoff", "value": 50, "minutes": 5})["ok"]
    for _ in range(240):
        e.step(1.0)
    for d in (e.smart, e.base):
        check_plans(d)
        off = d.stats()["off_duty"]
        assert 20 <= off <= 50 and np.array_equal(np.flatnonzero(d.f.off_until > 0), np.flatnonzero(e.smart.f.off_until > 0))
    for _ in range(400):
        e.step(1.0)
    assert e.smart.stats()["off_duty"] == 0 and e.base.stats()["off_duty"] == 0     # back after five minutes
    assert "s" in e.frame("smart")["agents"]


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


# ---------------------------------------------------------------------------- electric fleet
@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("policy", ["smart", "naive"])
def test_electric_fleet_charges_and_never_runs_empty(city, policy):
    from orca.dispatch import CHARGE, PLUGGED
    e = Engine(seed=2, fleet=250, use_learned=False, city=city, ev=policy)
    for d in (e.smart, e.base):
        d.f.soc[:] = np.linspace(0.12, 0.6, d.n)               # many cars low from the start
    for _ in range(1800):
        e.step(1.0)
    for d in (e.smart, e.base):
        ev, f = d.ev, d.f
        check_plans(d)
        assert ev.m["sessions"] > 0 and ev.m["stranded"] == 0 and (f.soc > 0).all()
        assert (ev.used <= ev.plugs).all() and ev.used.sum() == ev.plugged.sum()
        assert sum(len(q) for q in ev.queue) == int(np.count_nonzero((f.state == PLUGGED) & ~ev.plugged))
        assert (ev.coming == np.bincount(ev.station[f.state == CHARGE], minlength=ev.ns)).all()
        assert (f.soc[ev.plugged] < C.EV_TARGET + 0.05).all()
        busy = (f.state == PICKUP) | (f.state == TRIP)
        assert (f.soc[busy] > 0.02).all(), "rides go only to cars with charge enough to finish them"
    fr = json.loads(json.dumps(e.frame("smart")))
    assert min(fr["agents"]["b"]) >= 0 and max(fr["agents"]["b"]) <= 100
    s = json.loads(json.dumps(e.summary()))
    assert len(s["fx"]["chargers"]["smart"]) == e.smart.ev.ns


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
        # what if: an incident where the operator clicked, drivers logging off, shared rides and an electric fleet
        assert requests.post(base + "/api/command", json={"cmd": "accident", "x": 50, "z": 60}).json()["ok"]
        assert requests.post(base + "/api/command", json={"cmd": "accident", "x": "here"}).status_code == 400
        assert requests.post(base + "/api/command", json={"cmd": "logoff", "value": 40, "minutes": 10}).json()["ok"]
        r = requests.post(base + "/api/reset", json={"fleet": 300, "pool": True, "ev": "smart"}).json()
        assert r["ok"] and r["pool"] and r["ev"] == "smart"
        assert requests.post(base + "/api/reset", json={"ev": "diesel"}).status_code == 400
        assert requests.get(base + "/api/health").json()["pool"] is True
        with requests.get(base + "/api/stream?view=smart&enc=b64", stream=True, timeout=5) as r:
            for line in r.iter_lines(decode_unicode=True):
                if line.startswith("data:") and '"type":"frame"' in line:
                    a = json.loads(line[5:])["agents"]
                    break
        assert isinstance(a["x"], str) and a["n"] == 300 and {"o", "b"} <= set(a)
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


# ---------------------------------------------------------------------------- learned repositioning
def test_value_network_gradients_match_finite_differences():
    from orca import rl
    rng = np.random.default_rng(0)
    net = rl.ValueNet(5, seed=1)
    z, X, y = rng.integers(0, 5, 16), rng.normal(size=(16, rl.N_FEAT)), rng.normal(5, 0.3, 16)
    _, g = net.grads(z, X, y)
    for k in ("W1", "W2", "w3", "E", "b1"):
        idx = tuple(rng.integers(0, s) for s in net.p[k].shape)
        old = net.p[k][idx]
        net.p[k][idx] = old + 1e-6
        up = net.grads(z, X, y)[0]
        net.p[k][idx] = old - 1e-6
        down = net.grads(z, X, y)[0]
        net.p[k][idx] = old
        assert g[k][idx] == pytest.approx((up - down) / 2e-6, rel=1e-3, abs=1e-7)


def test_fitted_td_learns_a_known_value():
    from orca import rl
    rng = np.random.default_rng(2)
    n = 3000
    z = rng.integers(0, 2, n)                        # zone 1 gets a ride every 5 minutes, zone 0 every 20, forever
    dt = np.where(z == 1, 300.0, 1200.0)
    X = np.zeros((n, rl.N_FEAT))
    net = rl.ValueNet(2, seed=0)
    rl.fit_td(net, dict(z0=z, X0=X, z1=z, X1=X, dt=dt, alive=np.ones(n, bool), reward=np.ones(n)), rounds=40, epochs=3, lr=5e-3)
    want = [1 / (1 - rl.GAMMA ** (dt_ / 60)) for dt_ in (1200.0, 300.0)]
    got = net(np.array([0, 1]), np.zeros((2, rl.N_FEAT)))
    assert got[0] == pytest.approx(want[0], rel=0.1) and got[1] == pytest.approx(want[1], rel=0.1)


@pytest.mark.skipif("manhattan" not in CITIES, reason="street network not built")
def test_repositioning_modes():
    from orca import rl
    if not rl.net_path("manhattan").exists():
        pytest.skip("no trained repositioning model (python -m orca.rl train)")
    for mode in ("learned", "off"):
        e = Engine(seed=8, fleet=400, use_learned=False, city="manhattan", rebalance=mode)
        moved = []
        for _ in range(600):
            before = int(np.count_nonzero(e.smart.f.rebal))
            e.step(1.0)
            moved.append(int(np.count_nonzero(e.smart.f.rebal)) - before)
        if mode == "off":
            assert not e.smart.f.rebal.any()
        else:
            assert e.smart.learned.moves > 0
            assert max(moved) <= int(np.ceil(400 * 0.015))     # never more than the round's budget


# ---------------------------------------------------------------------------- production stack
class _FakeRedis:
    """Enough of redis-py for the event writer: pipeline().xadd(...).execute(), optionally failing."""

    def __init__(self, fail_first=0):
        self.streams, self.fail = {}, fail_first

    def pipeline(self, transaction=False):
        fake, ops = self, []

        class P:
            def xadd(self, name, fields, maxlen=None, approximate=True):
                ops.append((name, fields))

            def execute(self):
                if fake.fail > 0:
                    fake.fail -= 1
                    raise ConnectionError("broker down")
                for name, fields in ops:
                    fake.streams.setdefault(name, []).append(fields)
        return P()


def test_event_stream_publishes_everything_and_survives_a_broker_outage():
    from orca.events import EventBus
    fake = _FakeRedis(fail_first=1)
    bus = EventBus(client=fake)
    for k in range(1200):
        bus.emit("pickup", req=k, wait=12.5)
    deadline = time.time() + 15
    while bus.published < 1200 and time.time() < deadline:
        time.sleep(0.05)
    bus.close()
    got = fake.streams["orca:events"]
    assert bus.errors > 0 and bus.dropped == 0                                    # one failed batch was retried later
    assert {int(e["req"]) for e in got} >= set(range(1200 - bus.errors, 1200))
    assert [e["seq"] for e in bus.since(1195)] == [1196, 1197, 1198, 1199, 1200]
    assert any(line.startswith('orca_events_total{sink="redis",outcome="published"}') for line in bus.metrics())


def test_engine_emits_domain_events_in_order():
    e = Engine(seed=3, fleet=300, use_learned=False, city="grid", events=True)
    for _ in range(600):
        e.step(1.0)
    ev = e.bus.since(0, 100000)
    kinds = {x["kind"] for x in ev}
    assert {"request", "assign", "pickup", "dropoff"} <= kinds
    assert [x["seq"] for x in ev] == sorted(x["seq"] for x in ev)
    first = {}
    for x in ev:                                                                   # a rider is requested, assigned, picked up
        if x["kind"] in ("request", "assign", "pickup") and x.get("policy", "smart") == "smart":
            first.setdefault((x["req"], x["kind"]), x["seq"])
    for (req, kind), seq in first.items():
        if kind == "pickup":
            assert first[(req, "request")] < first[(req, "assign")] < seq
    m = e.prometheus()
    assert 'orca_match_latency_ms_bucket{le="+Inf"}' in m and "orca_events_total" in m


@pytest.mark.skipif(not __import__("os").environ.get("ORCA_TEST_PG"), reason="set ORCA_TEST_PG to a PostgreSQL URL")
def test_ledger_on_postgres_matches_sqlite():
    import os

    from orca.ledger import Ledger, lost, trips
    e = Engine(seed=5, fleet=300, use_learned=False, city="grid")
    for _ in range(900):
        e.step(1.0)
    e.ledger.flush()
    pg = Ledger(os.environ["ORCA_TEST_PG"], run=f"test-{time.time()}")      # the same rows, written to PostgreSQL
    with e.ledger.db.connect() as cx:
        pg.buf_trips = [(pg.run, *r[1:]) for r in cx.execute(trips.select()).fetchall()]
        pg.buf_lost = [(pg.run, *r[1:]) for r in cx.execute(lost.select()).fetchall()]
    a, b = e.ledger.analytics(), pg.analytics()
    for pol in ("smart", "base"):
        assert a[pol]["trips"] == b[pol]["trips"] and a[pol]["lost"] == b[pol]["lost"]
        assert a[pol]["mean"] == pytest.approx(b[pol]["mean"])
        assert [(h["hour"], h["trips"]) for h in a[pol]["hourly"]] == [(h["hour"], h["trips"]) for h in b[pol]["hourly"]]
        assert [h["mean"] for h in a[pol]["hourly"]] == pytest.approx([h["mean"] for h in b[pol]["hourly"]])


@pytest.mark.skipif(not __import__("os").environ.get("ORCA_TEST_REDIS"), reason="set ORCA_TEST_REDIS to a Redis URL")
def test_event_stream_on_real_redis():
    import os

    import redis

    from orca.events import EventBus
    stream = f"orca:test:{time.time()}"
    bus = EventBus(url=os.environ["ORCA_TEST_REDIS"], stream=stream)
    for k in range(300):
        bus.emit("pickup", req=k)
    bus.close()
    r = redis.Redis.from_url(os.environ["ORCA_TEST_REDIS"])
    assert r.xlen(stream) == 300
    r.delete(stream)


# ---------------------------------------------------------------------------- any city on demand
def test_local_projection_matches_great_circle_distances():
    from orca.geo import to_local
    lat0, lon0 = 42.37, -71.11
    e, n = to_local([-71.11, -71.05, -71.11], [42.37, 42.37, 42.42], lat0, lon0)
    hav = lambda la1, lo1, la2, lo2: 2 * 6371008.8 * math.asin(math.sqrt(
        math.sin(math.radians(la2 - la1) / 2) ** 2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(math.radians(lo2 - lo1) / 2) ** 2))
    assert e[0] == n[0] == 0
    assert math.hypot(e[1], n[1]) == pytest.approx(hav(lat0, lon0, 42.37, -71.05), rel=3e-3)
    assert math.hypot(e[2], n[2]) == pytest.approx(hav(lat0, lon0, 42.42, -71.11), rel=3e-3)


@pytest.mark.parametrize("deg", [0.0, 12.5, 29.0, -20.0, 44.0])
def test_street_grid_angle_is_found(deg):
    from orca.newcity import grid_angle
    rng = np.random.default_rng(0)
    a = math.radians(deg)
    x0, y0 = rng.uniform(0, 5000, 4000), rng.uniform(0, 5000, 4000)
    along = rng.integers(0, 2, 4000) * (math.pi / 2) + a + rng.normal(0, 0.03, 4000)   # two perpendicular street families
    ln = rng.uniform(40, 200, 4000)
    got = grid_angle(x0, y0, x0 + ln * np.sin(along), y0 + ln * np.cos(along))
    assert abs((got - deg + 45) % 90 - 45) < 1.5


def test_zones_cover_the_streets_and_take_place_names():
    from orca.newcity import make_zones
    rng = np.random.default_rng(1)
    e, n = rng.uniform(0, 3000, 5000), rng.uniform(0, 2000, 5000)
    keep = ~((e > 2000) & (n > 1000))                                 # a lake in one corner: no streets
    places = [(450.0, 450.0, "Old Town", 0), (1500.0, 1500.0, "Harbour", 2)]
    z = make_zones(e[keep], n[keep], places, 1000.0, 25, "Testville")
    names = [x["name"] for x in z]
    assert len(z) == 5 and "Old Town" in names and "Harbour" in names
    assert len(set(names)) == len(names)
    for x in z:
        (x0, y0), (x1, _), (_, y1) = x["rings"][0][:3]
        assert x1 - x0 == pytest.approx(1000.0) and y1 - y0 == pytest.approx(1000.0)
