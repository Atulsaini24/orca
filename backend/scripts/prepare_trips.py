"""Turn a city's real trip records into ORCA's demand replay, traffic profile and browser sample.

    python scripts/prepare_trips.py --city manhattan     # NYC TLC, September 2026: yellow taxis + Uber / Lyft
    python scripts/prepare_trips.py --city chicago       # City of Chicago TNP (Uber / Lyft / Via), September 2026

Run scripts/build_streets.py for the city first: trips are projected into its street network's frame.

Outputs
  data/trips_<city>.parquet            trips with projected pickup / dropoff positions (metres, the city's projection)
  artifacts/traffic_<city>.json        real median speed by hour, and by zone x hour (from short trips)
  artifacts/real_waits_<city>.json     (Manhattan) real Uber / Lyft rider waits, request -> driver on scene
  frontend/data/<city>/trips.js        a 24,000-trip sample and the hourly profile for the browser engine

Only ordinary weekdays are kept (Labor Day, 7 September 2026, is excluded), and only trips that start
and end inside the city's zones.
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from orca import cityspec  # noqa: E402

DATA = ROOT / "data"
HOLIDAYS = {pd.Timestamp("2026-09-07").date()}                    # Labor Day
MILE = 1609.344


# ---------------------------------------------------------------------------- loading, per city
def _weekday(t: pd.Series) -> pd.Series:
    return (t.dt.dayofweek < 5) & ~t.dt.date.isin(HOLIDAYS)


def load_manhattan(spec, rng) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Yellow taxis and high-volume for-hire (Uber, Lyft) trips, both with TLC zone ids."""
    zones = spec.zones()
    frames, waits = [], None
    y = DATA / "yellow_tripdata_2026-09.parquet"
    if y.exists():
        d = pd.read_parquet(y, columns=["tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID", "trip_distance"])
        frames.append(pd.DataFrame({"pickup": d.tpep_pickup_datetime, "dropoff": d.tpep_dropoff_datetime, "pu": d.PULocationID,
                                    "do": d.DOLocationID, "miles": d.trip_distance, "service": "yellow taxi"}))
    h = DATA / "fhvhv_tripdata_2026-09.parquet"
    if h.exists():
        d = pd.read_parquet(h, columns=["hvfhs_license_num", "request_datetime", "on_scene_datetime", "pickup_datetime",
                                        "dropoff_datetime", "PULocationID", "DOLocationID", "trip_miles"])
        frames.append(pd.DataFrame({"pickup": d.pickup_datetime, "dropoff": d.dropoff_datetime, "pu": d.PULocationID,
                                    "do": d.DOLocationID, "miles": d.trip_miles,
                                    "service": d.hvfhs_license_num.map({"HV0003": "Uber", "HV0005": "Lyft"}).fillna("other")}))
        man = d.PULocationID.isin(zones) & d.DOLocationID.isin(zones) & _weekday(d.request_datetime)
        w = pd.DataFrame({"request": d.request_datetime[man], "on_scene": d.on_scene_datetime[man], "pickup": d.pickup_datetime[man],
                          "service": d.hvfhs_license_num[man]})
        waits = w
    if not frames:
        raise SystemExit("no September 2026 TLC files in data/: run scripts/download_tlc.py first")
    df = pd.concat(frames, ignore_index=True)
    df = df[df.pu.isin(zones) & df.do.isin(zones)].reset_index(drop=True)
    pools = {z: _point_pool(r, 2500, rng) for z, (r, _) in zones.items()}
    df["pu_e"], df["pu_n"] = _place(df.pu.to_numpy(), pools, rng)
    df["do_e"], df["do_n"] = _place(df.do.to_numpy(), pools, rng)
    return df, waits


def load_chicago(spec, rng) -> tuple[pd.DataFrame, None]:
    """TNP trips: pickup / dropoff census-tract centroid (or community-area centroid when the tract is withheld)."""
    from matplotlib.path import Path as MPath
    src = DATA / "chicago" / "tnp_trips_2026-09.csv"
    if not src.exists():
        raise SystemExit("data/chicago/tnp_trips_2026-09.csv missing: run scripts/download_chicago.py")
    d = pd.read_csv(src)
    t0 = pd.to_datetime(d.trip_start_timestamp)
    # timestamps are rounded to 15 minutes for privacy: spread each trip uniformly within its window
    off = pd.to_timedelta(rng.uniform(0, 900, len(d)), unit="s")
    pickup = t0 + off
    df = pd.DataFrame({"pickup": pickup, "dropoff": pickup + pd.to_timedelta(d.trip_seconds.fillna(0), unit="s"),
                       "miles": d.trip_miles, "service": "TNP", "pu_ca": d.pickup_community_area, "do_ca": d.dropoff_community_area})
    zones = spec.zones()
    zpaths = {z: MPath(np.vstack(r)) for z, (r, _) in zones.items()}
    ca_centre = {}
    for (ca, lat, lon), n in d.groupby(["pickup_community_area", "pickup_centroid_latitude", "pickup_centroid_longitude"]).size().items():
        if n > ca_centre.get(ca, (None, 0))[1]:
            ca_centre[ca] = ((lat, lon), n)                               # most frequent point = the area's own centroid
    for end, la, lo, ca in (("pu", "pickup_centroid_latitude", "pickup_centroid_longitude", "pickup_community_area"),
                            ("do", "dropoff_centroid_latitude", "dropoff_centroid_longitude", "dropoff_community_area")):
        lat, lon = d[la].to_numpy(dtype=float), d[lo].to_numpy(dtype=float)
        e, n = spec.project(lon, lat)
        coarse = np.array([ca_centre.get(c, ((None, None), 0))[0] == (a, b) for c, a, b in zip(d[ca], d[la], d[lo])])
        sigma = np.where(coarse, 650.0, 120.0)                            # community-area centroid: spread wider
        e = e + rng.normal(0, 1, len(e)) * sigma
        n = n + rng.normal(0, 1, len(n)) * sigma
        z = np.full(len(e), -1)
        pts = np.c_[e, n]
        for zid, path in zpaths.items():
            hit = path.contains_points(pts) & (z < 0)
            z[hit] = zid
        df[f"{end}_e"], df[f"{end}_n"], df[end] = e, n, z
    df = df[(df.pu >= 0) & (df["do"] >= 0) & np.isfinite(df.pu_e) & np.isfinite(df.do_e)].reset_index(drop=True)
    return df, None


def _point_pool(rings, n, rng):
    sys.path.insert(0, str(HERE))
    import prepare_tlc as P
    return P.point_pool([r / cityspec.FT for r in rings], n, rng)        # prepare_tlc works in feet and returns metres


def _place(ids, pools, rng):
    xy = np.empty((len(ids), 2))
    for z in np.unique(ids):
        m = ids == z
        pool = pools[z]
        xy[m] = pool[rng.integers(0, len(pool), m.sum())]
    return xy[:, 0], xy[:, 1]


# ---------------------------------------------------------------------------- outputs
def traffic_profile(df: pd.DataFrame, zone_ids: list[int], days: int) -> dict:
    dur = (df.dropoff - df.pickup).dt.total_seconds()
    speed = df.miles * MILE / dur
    hour = df.pickup.dt.hour
    ok = speed.between(0.5, 30) & (dur > 60)
    by_hour = speed[ok].groupby(hour[ok]).median().reindex(range(24)).interpolate().bfill().ffill()
    day_ref = float(by_hour.loc[8:19].mean())
    # local speeds from short trips, credited half to each end's zone, shrunk toward the city's short-trip speed
    short = ok & (df.miles <= 2.5)
    s = pd.concat([pd.DataFrame({"z": df.pu[short], "h": hour[short], "v": speed[short]}),
                   pd.DataFrame({"z": df["do"][short], "h": hour[short], "v": speed[short]})])
    city_short = s.groupby("h").v.median().reindex(range(24)).interpolate().bfill().ffill()
    g = s.groupby(["z", "h"]).v.agg(["median", "size"])
    K = 150.0
    table = []
    for z in zone_ids:
        row = []
        for h in range(24):
            med, n = (g.loc[(z, h)].tolist() if (z, h) in g.index else (city_short[h], 0))
            shrunk = (n * med + K * city_short[h]) / (n + K)
            row.append(round(float(by_hour[h] / day_ref * shrunk / city_short[h]), 4))
        table.append(row)
    hourly = (hour.value_counts().reindex(range(24), fill_value=0) / days).astype(float)
    return dict(speed_mps=[round(float(v), 3) for v in by_hour], factor=[round(float(v) / day_ref, 4) for v in by_hour],
                trips_per_hour=[round(float(v), 1) for v in hourly], zone_ids=zone_ids, zone_hour_factor=table,
                zone_hour_note="speed of short trips (<= 2.5 mi) touching the zone, relative to the city's daytime mean; "
                               f"shrunk toward the city with {int(K)} pseudo-trips")


def real_waits(w: pd.DataFrame) -> dict:
    on = (w.on_scene - w.request).dt.total_seconds()
    board = (w.pickup - w.request).dt.total_seconds()
    ok = on.between(0, 3600)
    h = w.request.dt.hour
    q = lambda x: {k: round(float(np.percentile(x, p)), 1) for k, p in (("p50", 50), ("p90", 90), ("p95", 95))}
    return dict(source="NYC TLC high-volume for-hire trips (Uber, Lyft), September 2026 weekdays, Manhattan to Manhattan",
                trips=int(ok.sum()), mean_on_scene_s=round(float(on[ok].mean()), 1), **{f"on_scene_{k}": v for k, v in q(on[ok]).items()},
                mean_to_board_s=round(float(board[board.between(0, 3600)].mean()), 1),
                by_hour=[round(float(on[ok & (h == k)].mean()), 1) for k in range(24)],
                by_service={s: round(float(on[ok & (w.service == s)].mean()), 1) for s in ("HV0003", "HV0005") if (ok & (w.service == s)).any()},
                note="request -> driver on scene, as recorded by the platforms; ORCA's wait is request -> car at the rider")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="manhattan", choices=list(cityspec.CITIES))
    ap.add_argument("--rows", type=int, default=2_000_000)
    ap.add_argument("--browser-rows", type=int, default=24_000)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    spec, rng = cityspec.get(a.city), np.random.default_rng(a.seed)
    df, waits = {"manhattan": load_manhattan, "chicago": load_chicago}[a.city](spec, rng)
    n_raw = len(df)
    dur = (df.dropoff - df.pickup).dt.total_seconds()
    keep = _weekday(df.pickup) & (df.pickup.dt.month == 9) & dur.between(60, 3 * 3600) & df.miles.between(0.1, 30)
    df = df[keep].reset_index(drop=True)
    days = df.pickup.dt.date.nunique()
    print(f"{spec.short}: {n_raw:,} trips inside the zones -> {len(df):,} on {days} ordinary weekdays "
          f"({', '.join(f'{k} {v:,}' for k, v in df.service.value_counts().items())})")
    zone_ids = sorted(spec.zones())
    prof = traffic_profile(df, zone_ids, days)
    prof.update(source=f"{spec.data_note}; {len(df):,} weekday trips over {days} days", city=spec.name, raw_trips=int(n_raw),
                kept_trips=len(df), days=int(days), zones=len(zone_ids), replay_trips=min(a.rows, len(df)),
                services={k: int(v) for k, v in df.service.value_counts().items()})
    spec.traffic.write_text(json.dumps(prof, indent=2))
    print("real speed by hour (m/s): " + " ".join(f"{v:.1f}" for v in prof["speed_mps"]))
    zt = np.asarray(prof["zone_hour_factor"])
    print(f"zone x hour speed factors at 18:00: min {zt[:, 18].min():.2f}, median {np.median(zt[:, 18]):.2f}, max {zt[:, 18].max():.2f}")
    if waits is not None:
        rw = real_waits(waits)
        (cityspec.ARTIFACTS / f"real_waits_{spec.name}.json").write_text(json.dumps(rw, indent=2))
        print(f"real Uber / Lyft waits: mean {rw['mean_on_scene_s']:.0f} s to driver on scene, p90 {rw['on_scene_p90']:.0f} s "
              f"({rw['trips']:,} rides)")

    if len(df) > a.rows:
        df = df.iloc[np.sort(rng.choice(len(df), a.rows, replace=False))].reset_index(drop=True)
    out = pd.DataFrame({"tpep_pickup_datetime": df.pickup, "tpep_dropoff_datetime": df.dropoff,
                        "PULocationID": df.pu.astype("int32"), "DOLocationID": df["do"].astype("int32"),
                        "trip_distance": df.miles.astype("float32"), "service": df.service.astype("category"),
                        "pickup_e": df.pu_e, "pickup_n": df.pu_n, "dropoff_e": df.do_e, "dropoff_n": df.do_n})
    out.to_parquet(spec.trips, index=False)
    print(f"wrote {spec.trips.name} ({len(out):,} trips, {spec.trips.stat().st_size / 1e6:.0f} MB)")

    # ---- browser sample, in the street city's world frame
    from orca import city as G
    from orca.demand import StreetProjector, load_trips
    c = G.use(spec.name)
    trips = load_trips(spec.trips, nrows=None)
    proj = StreetProjector(c)
    idx = np.sort(rng.choice(len(trips), min(a.browser_rows, len(trips)), replace=False))
    s = trips.iloc[idx]
    px, pz = proj(s.ex.values, s.ny.values)
    dx, dz = proj(s.dex.values, s.dny.values)
    order = np.argsort(s.sec.values, kind="stable")
    sec = s.sec.values[order].astype(np.uint32)
    res = 100 if max(c.WX, c.WZ) < 600 else 50
    q = lambda v: np.round(v[order] * res).astype(np.uint16)
    blob = sec.tobytes() + np.stack([q(px), q(pz), q(dx), q(dz)], 1).tobytes()
    hh = df.pickup.dt.hour
    peak_h = int(hh.value_counts().idxmax())
    dur_s = (df.dropoff - df.pickup).dt.total_seconds().to_numpy()
    meta = dict(city=spec.name, res=res, peak_trip_s=round(float(np.clip(dur_s[(hh == peak_h).to_numpy()], 60, 3600).mean()), 1),
                source=prof["source"], n=len(sec), days=int(days), total=len(trips), trips_per_hour=prof["trips_per_hour"],
                traffic=prof["factor"], zone_hour=prof["zone_hour_factor"])
    dest = ROOT.parent / "frontend" / "data" / spec.name / "trips.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("// Generated by backend/scripts/prepare_trips.py from real trip records. Do not edit.\n"
                    f"var TLC_DATA = {json.dumps(meta)};\nTLC_DATA.b64 = '{base64.b64encode(blob).decode()}';\n")
    print(f"wrote {dest.relative_to(ROOT.parent)} ({dest.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
