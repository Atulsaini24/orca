"""Turn the official NYC TLC trip records into ORCA's real demand and traffic inputs.

    python scripts/download_tlc.py --year 2015 --month 1          # 12.7M real yellow-taxi trips
    python scripts/prepare_tlc.py                                  # writes the three outputs below

The TLC now publishes every year, 2015 included, with pickup and dropoff
*taxi zone IDs* instead of GPS coordinates. This script uses the official zone
boundary shapefile and places each trip at a uniformly random point inside its
real pickup and dropoff zone polygons, so trips spread over the real street
area of each zone instead of collapsing onto 69 centroids.

Only Manhattan-to-Manhattan trips on ordinary weekdays are kept: New Year's
Day and 2 Jan, Martin Luther King Day (19 Jan), the 26-27 Jan blizzard travel
ban and all weekends are excluded so the replay is a typical working day.

Outputs
  data/nyc_tlc_2015-01_manhattan.parquet   trips with projected pickup / dropoff positions (metres)
  artifacts/nyc_traffic_profile.json       real Manhattan taxi speed by hour of day, from distance / duration
  frontend/src/tlc_data.js                 a compact sample and the real hourly profile for the browser engine
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import zone_centroids as zc  # noqa: E402

FT = 0.3048006096          # US survey foot in metres (the shapefile's unit)
ISLANDS = {103, 104, 105, 153, 194, 202}   # Governor's, Ellis, Liberty, Marble Hill, Randalls, Roosevelt
EXCLUDE_DAYS = {1, 2, 19, 26, 27}


def zone_polygons(shp: Path):
    shapes = zc.read_shp(shp)
    attrs = zc.read_dbf(shp.with_suffix(".dbf"))
    out = {}
    for s, a in zip(shapes, attrs):
        if s is None or a.get("borough") != "Manhattan":
            continue
        zid = int(float(a.get("LocationID") or a.get("OBJECTID")))
        if zid in ISLANDS:
            continue
        pts, parts = s
        bounds = list(parts) + [len(pts)]
        rings = [np.asarray(pts[bounds[k]:bounds[k + 1]], dtype=float) for k in range(len(parts))]
        out[zid] = (rings, a.get("zone", ""))
    return out


def inside(px, py, rings):
    """Even-odd point-in-polygon over all rings (holes included), vectorised over points."""
    res = np.zeros(len(px), dtype=bool)
    for r in rings:
        x0, y0 = r[:, 0], r[:, 1]
        x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
        for a, b, c, d in zip(x0, y0, x1, y1):
            if b == d:
                continue
            crosses = (b > py) != (d > py)
            xi = a + (py - b) * (c - a) / (d - b)
            res ^= crosses & (px < xi)
    return res


def point_pool(rings, n, rng):
    allp = np.concatenate(rings)
    (xmin, ymin), (xmax, ymax) = allp.min(0), allp.max(0)
    got = []
    while sum(len(g) for g in got) < n:
        x = rng.uniform(xmin, xmax, 4 * n)
        y = rng.uniform(ymin, ymax, 4 * n)
        m = inside(x, y, rings)
        got.append(np.c_[x[m], y[m]])
    return np.concatenate(got)[:n] * FT


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trips", default=str(ROOT / "data/yellow_tripdata_2015-01.parquet"))
    ap.add_argument("--shp", default=str(ROOT / "data/taxi_zones/taxi_zones/taxi_zones.shp"))
    ap.add_argument("--out", default=str(ROOT / "data/nyc_tlc_2015-01_manhattan.parquet"))
    ap.add_argument("--rows", type=int, default=2_000_000, help="trips to keep for the server replay")
    ap.add_argument("--browser-rows", type=int, default=24_000, help="trips embedded in the browser engine")
    ap.add_argument("--seed", type=int, default=2015)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    zones = zone_polygons(Path(a.shp))
    print(f"{len(zones)} Manhattan taxi zones from the official TLC shapefile")

    cols = ["tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID", "trip_distance"]
    df = pd.read_parquet(a.trips, columns=cols)
    n_raw = len(df)
    t0, t1 = df.tpep_pickup_datetime, df.tpep_dropoff_datetime
    dur = (t1 - t0).dt.total_seconds()
    keep = (df.PULocationID.isin(zones) & df.DOLocationID.isin(zones) & (t0.dt.year == 2015) & (t0.dt.month == 1)
            & (t0.dt.dayofweek < 5) & ~t0.dt.day.isin(EXCLUDE_DAYS) & dur.between(60, 3 * 3600)
            & df.trip_distance.between(0.1, 30))
    df = df[keep].reset_index(drop=True)
    dur = dur[keep].reset_index(drop=True)
    days = df.tpep_pickup_datetime.dt.date.nunique()
    print(f"{n_raw:,} raw trips -> {len(df):,} Manhattan weekday trips over {days} days")

    # ---- real traffic: median taxi speed by hour of day ---------------------------------
    hour = df.tpep_pickup_datetime.dt.hour
    speed = df.trip_distance * 1609.344 / dur
    ok = speed.between(0.5, 30)
    by_hour = speed[ok].groupby(hour[ok]).median().reindex(range(24)).interpolate().bfill().ffill()
    day_ref = float(by_hour.loc[8:19].mean())
    hourly_trips = (hour.value_counts().reindex(range(24), fill_value=0) / days).astype(float)
    profile = dict(source=f"NYC TLC yellow taxi trips, January 2015, {len(df):,} Manhattan weekday trips over {days} days",
                   raw_trips=int(n_raw), kept_trips=len(df), days=int(days), zones=len(zones), replay_trips=min(a.rows, len(df)),
                   speed_mps=[round(float(v), 3) for v in by_hour],
                   factor=[round(float(v) / day_ref, 4) for v in by_hour],
                   trips_per_hour=[round(float(v), 1) for v in hourly_trips])
    art = ROOT / "artifacts" / "nyc_traffic_profile.json"
    art.write_text(json.dumps(profile, indent=2))
    print("real speed by hour (m/s): " + " ".join(f"{v:.1f}" for v in by_hour))

    # ---- sample and place inside real zone polygons ------------------------------------
    if len(df) > a.rows:
        df = df.iloc[np.sort(rng.choice(len(df), a.rows, replace=False))].reset_index(drop=True)
    pools = {z: point_pool(r, 3000, rng) for z, (r, _) in zones.items()}

    def place(ids):
        xy = np.empty((len(ids), 2))
        ids = ids.to_numpy()
        for z in np.unique(ids):
            m = ids == z
            xy[m] = pools[z][rng.integers(0, len(pools[z]), m.sum())]
        return xy

    pu, do = place(df.PULocationID), place(df.DOLocationID)
    org = (0.0, 0.0)                    # absolute EPSG:2263 metres, so any city frame can be applied later
    out = pd.DataFrame({
        "tpep_pickup_datetime": df.tpep_pickup_datetime, "tpep_dropoff_datetime": df.tpep_dropoff_datetime,
        "PULocationID": df.PULocationID.astype("int16"), "DOLocationID": df.DOLocationID.astype("int16"),
        "trip_distance": df.trip_distance.astype("float32"),
        "pickup_e": (pu[:, 0] - org[0]).astype("float32"), "pickup_n": (pu[:, 1] - org[1]).astype("float32"),
        "dropoff_e": (do[:, 0] - org[0]).astype("float32"), "dropoff_n": (do[:, 1] - org[1]).astype("float32"),
    })
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(a.out, index=False)
    print(f"wrote {a.out} ({len(out):,} trips, {Path(a.out).stat().st_size / 1e6:.0f} MB)")

    # mean real trip duration in the busiest hour: sizes the browser engine's demand like the server's
    hh = df.tpep_pickup_datetime.dt.hour
    peak_h = int(hh.value_counts().idxmax())
    dur_s = (df.tpep_dropoff_datetime - df.tpep_pickup_datetime).dt.total_seconds().to_numpy()
    peak_trip = float(np.clip(dur_s[(hh == peak_h).to_numpy()], 60, 3600).mean())

    # ---- browser sample: projected to city units, quantised --------------------------------
    from orca import city as G
    from orca.demand import Projector, StreetProjector, load_trips
    city = G.use()
    trips = load_trips(a.out, nrows=None)
    proj = StreetProjector(city) if hasattr(city, "frame") else Projector(trips)
    idx = np.sort(rng.choice(len(trips), min(a.browser_rows, len(trips)), replace=False))
    s = trips.iloc[idx]
    px, pz = proj(s.ex.values, s.ny.values)
    dx, dz = proj(s.dex.values, s.dny.values)
    order = np.argsort(s.sec.values, kind="stable")
    sec = s.sec.values[order].astype(np.uint32)
    res = 100 if max(city.WX, city.WZ) < 600 else 50                 # quantisation steps per unit (fits uint16)
    q = lambda v: np.round(v[order] * res).astype(np.uint16)
    blob = sec.tobytes() + np.stack([q(px), q(pz), q(dx), q(dz)], 1).tobytes()
    meta = dict(city=city.name, res=res, peak_trip_s=round(peak_trip, 1), source=profile["source"], n=len(sec), days=int(days),
                total=len(trips), trips_per_hour=profile["trips_per_hour"], traffic=profile["factor"])
    js = ("// Generated by backend/scripts/prepare_tlc.py from real NYC TLC trip records. Do not edit.\n"
          f"const TLC_DATA = {json.dumps(meta)};\n"
          f"TLC_DATA.b64 = '{base64.b64encode(blob).decode()}';\n")
    dest = ROOT.parent / "frontend" / "src" / "tlc_data.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(js)
    print(f"wrote {dest} ({len(js) / 1024:.0f} KB, {len(sec):,} trips)")


if __name__ == "__main__":
    main()
