"""Correctness tests for routing, dispatch invariants, matching, data loading and the API."""
import json
import random
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from orca import config as C
from orca.city import CELL, is_arterial, on_road, route, snap
from orca.dispatch import OrcaDispatcher
from orca.engine import Engine

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------- routing
def test_routes_stay_on_roads_and_reach_target():
    rng = random.Random(0)
    for _ in range(2000):
        if rng.random() < 0.5:
            x, z = rng.random() * C.W, float(rng.randrange(C.GRID) * CELL)
        else:
            x, z = float(rng.randrange(C.GRID) * CELL), rng.random() * C.W
        tx, tz = snap(rng.random() * C.W), snap(rng.random() * C.W)
        pts = route(x, z, tx, tz, rng.randrange(1000))
        px, pz = x, z
        for wx, wz in pts:
            assert (wx == px) or (wz == pz), "every leg moves along one axis"
            assert on_road(wx, wz)
            px, pz = wx, wz
        assert (px, pz) == (tx, tz)


def test_arterials():
    assert is_arterial(0) and is_arterial(7) and is_arterial(C.GRID - 1)
    assert not is_arterial(3) and not is_arterial(3.5)


# ---------------------------------------------------------------------------- engine invariants
@pytest.fixture(scope="module")
def engine():
    e = Engine(seed=5, fleet=500, use_learned=False)
    for i in range(1200):
        e.step(1.0)
        if i == 400:
            e.world.trigger_spike()
            e.world.trigger_outage()
    return e


@pytest.mark.parametrize("which", ["smart", "base"])
def test_no_double_assignment(engine, which):
    d = engine.smart if which == "smart" else engine.base
    seen = {}
    for a in d.agents:
        if a.rs is not None:
            assert a.rs.r.id not in seen, f"request {a.rs.r.id} held by two cars"
            seen[a.rs.r.id] = a.id
            if a.state in ("offered", "pickup"):
                assert a.rs.agent is a
        if a.state == "idle":
            assert a.rs is None


def test_both_policies_see_identical_demand(engine):
    a, b = engine.smart, engine.base
    total = engine.world.next_req - 1
    for d in (a, b):
        in_flight = sum(1 for ag in d.agents if ag.state == "trip")
        assert d.m["served"] + d.m["lost"] + len(d.active) <= total
        assert d.m["served"] >= in_flight


def test_orca_beats_nearest_on_mean_wait(engine):
    assert engine.smart.stats()["mean"] < engine.base.stats()["mean"]


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
    assert a["smart"]["mean"] < a["base"]["mean"]
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
    assert checked > 50
    s = engine.summary()
    if s["notable"]:
        assert s["notable"]["timeline"][0]["k"] == "request"
    assert len(s["zones"]["smart"]) == C.ZONES * C.ZONES and len(s["hist"]["base"]) == 12


# ---------------------------------------------------------------------------- matching
class _A:
    def __init__(self, i):
        self.id = i


class _RS:
    def __init__(self, cands):
        self.cands = cands


def test_hungarian_never_worse_than_greedy():
    rng = np.random.default_rng(1)
    d = OrcaDispatcher.__new__(OrcaDispatcher)
    for trial in range(50):
        nq, nu = rng.integers(2, 12), rng.integers(2, 12)
        units = [_A(i) for i in range(nu)]
        q = []
        for _ in range(nq):
            pick = rng.choice(nu, size=min(nu, 4), replace=False)
            q.append(_RS([dict(a=units[j], adj=float(rng.uniform(5, 200))) for j in pick]))
        C.HUNGARIAN_MAX = 10_000
        hung = d.solve(q)
        C.HUNGARIAN_MAX = -1
        greedy = d.solve(q)
        C.HUNGARIAN_MAX = 150
        cost = lambda m: (len(m), -sum(c["adj"] for _, c in m))
        # Hungarian assigns at least as many requests, and at no higher cost for the same count.
        assert cost(hung) >= cost(greedy) or len(hung) > len(greedy)
        used = [c["a"].id for _, c in hung]
        assert len(used) == len(set(used))


# ---------------------------------------------------------------------------- real-data path
def test_tlc_loader_and_replay(tmp_path):
    p = tmp_path / "trips.csv"
    rng = random.Random(3)
    rows = ["tpep_pickup_datetime,tpep_dropoff_datetime,pickup_longitude,pickup_latitude,dropoff_longitude,dropoff_latitude"]
    for i in range(3000):
        h, m = rng.randrange(24), rng.randrange(60)
        plat, plon = 40.72 + rng.random() * 0.07, -73.99 + rng.random() * 0.03
        rows.append(f"2015-01-13 {h:02d}:{m:02d}:00,2015-01-13 {h:02d}:{m:02d}:59,{plon},{plat},{plon + 0.005},{plat + 0.004}")
    rows.append("2015-01-13 10:00:00,2015-01-13 10:10:00,0,0,0,0")              # junk row is filtered
    p.write_text("\n".join(rows))
    e = Engine(seed=1, fleet=300, demand="tlc", data=str(p), use_learned=False)
    src = e.world.source
    assert src.n == 3000
    assert 0 <= src.px.min() and src.px.max() <= C.W and 0 <= src.pz.min() and src.pz.max() <= C.W
    got = 0
    for _ in range(600):
        e.step(1.0)
    got = e.world.next_req - 1
    assert got > 0


REAL = ROOT / "data" / "nyc_tlc_2015-01_manhattan.parquet"


@pytest.mark.skipif(not REAL.exists(), reason="real data not prepared (make data)")
def test_real_nyc_replay():
    e = Engine(seed=1, fleet=600, demand="tlc", data=str(REAL), use_learned=False)
    src = e.world.source
    assert src.n > 1_000_000 and src.days >= 15
    assert e.demand_label() == "real NYC TLC taxi trips"
    assert e.world.traffic is not None and len(e.world.traffic) == 24
    hourly = np.bincount((src.sec // 3600).astype(int), minlength=24)
    assert hourly[18] > 3 * hourly[4]                                   # real evening peak vs. 4 am lull
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
    proc = subprocess.Popen([sys.executable, "-m", "orca.server", "--port", str(port), "--fleet", "300", "--demand", "synthetic"],
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
        assert set(an) == {"smart", "base"} and len(an["smart"]["zones"]) == 49
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
