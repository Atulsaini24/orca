"""Replay of real NYC taxi trips (NYC TLC trip records) as the request stream.

Supported inputs (CSV, or Parquet when pyarrow is installed):

* The prepared file from ``scripts/prepare_tlc.py`` (the default): real trips with
  ``pickup_e, pickup_n, dropoff_e, dropoff_n`` in metres, placed inside their
  real TLC taxi-zone polygons.
* Trip files with coordinates (the 2009-2015 yellow taxi schema):
  ``pickup_longitude, pickup_latitude, dropoff_longitude, dropoff_latitude``
* Trip files with zone IDs (2016+): ``PULocationID, DOLocationID`` plus a
  centroid table produced by ``scripts/zone_centroids.py``.

Coordinates are projected to metres, rotated by Manhattan's 29 degree street
grid angle so avenues line up with the simulated grid, then fitted to the
simulated city. Trips from all days in the file are folded into one typical
day and thinned so the request rate matches the simulated fleet's capacity,
while keeping the real spatial and time-of-day pattern.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd

from . import config as C
from .city import zone_of

MANHATTAN_ANGLE = math.radians(29.0)
TIME_COLS = ["tpep_pickup_datetime", "pickup_datetime", "lpep_pickup_datetime", "Trip_Pickup_DateTime"]
DROP_TIME_COLS = ["tpep_dropoff_datetime", "dropoff_datetime", "lpep_dropoff_datetime", "Trip_Dropoff_DateTime"]
PROJECTED = ["pickup_e", "pickup_n", "dropoff_e", "dropoff_n"]
LONLAT = ["pickup_longitude", "pickup_latitude", "dropoff_longitude", "dropoff_latitude"]


def _read(path: Path, nrows: int | None) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
        return df.head(nrows) if nrows else df
    return pd.read_csv(path, nrows=nrows)


def load_trips(path: str | Path, zones: str | Path | None = None, nrows: int | None = 2_000_000) -> pd.DataFrame:
    """Return a clean frame with columns: date, sec, ex, ny, dex, dny, dur (metres / seconds)."""
    path = Path(path)
    df = _read(path, nrows)
    df.columns = [c.strip() for c in df.columns]
    tcol = next((c for c in TIME_COLS if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"No pickup time column found. Expected one of {TIME_COLS}")
    dcol = next((c for c in DROP_TIME_COLS if c in df.columns), None)
    t = pd.to_datetime(df[tcol], errors="coerce")
    out = pd.DataFrame({"date": t.dt.date, "sec": t.dt.hour * 3600 + t.dt.minute * 60 + t.dt.second})
    out["dur"] = (pd.to_datetime(df[dcol], errors="coerce") - t).dt.total_seconds() if dcol else np.nan

    if all(c in df.columns for c in PROJECTED):
        out["ex"], out["ny"], out["dex"], out["dny"] = (df[c].astype(float).values for c in PROJECTED)
    elif all(c in df.columns for c in LONLAT):
        lon, lat, dlon, dlat = (df[c].astype(float) for c in LONLAT)
        box = lat.between(40.70, 40.88) & lon.between(-74.02, -73.91) & dlat.between(40.70, 40.88) & dlon.between(-74.02, -73.91)
        lat0, lon0 = 40.78, -73.97
        kx = 111_320 * math.cos(math.radians(lat0))
        out["ex"], out["ny"] = (lon - lon0) * kx, (lat - lat0) * 110_540
        out["dex"], out["dny"] = (dlon - lon0) * kx, (dlat - lat0) * 110_540
        out = out[box.values]
    elif "PULocationID" in df.columns and zones is not None:
        zt = pd.read_csv(zones).set_index("LocationID")
        pu, do = df["PULocationID"].map(zt["x"]), df["PULocationID"].map(zt["y"])
        out["ex"], out["ny"] = pu.values, do.values
        out["dex"] = df["DOLocationID"].map(zt["x"]).values
        out["dny"] = df["DOLocationID"].map(zt["y"]).values
        if "borough" in zt.columns:
            man = df["PULocationID"].map(zt["borough"]).eq("Manhattan") & df["DOLocationID"].map(zt["borough"]).eq("Manhattan")
            out = out[man.values]
    else:
        raise ValueError("Trip file has neither lon/lat columns nor PULocationID (with --zones centroid table).")

    out = out.dropna(subset=["sec", "ex", "ny", "dex", "dny"])
    return out.reset_index(drop=True)


class Projector:
    """Rotate to Manhattan's grid and fit to the simulated city using robust percentiles."""

    def __init__(self, df: pd.DataFrame):
        c, s = math.cos(-MANHATTAN_ANGLE), math.sin(-MANHATTAN_ANGLE)
        self.c, self.s = c, s
        u = np.concatenate([df.ex * c - df.ny * s, df.dex * c - df.dny * s])
        v = np.concatenate([df.ex * s + df.ny * c, df.dex * s + df.dny * c])
        self.u0, self.u1 = np.percentile(u, [1, 99])
        self.v0, self.v1 = np.percentile(v, [1, 99])

    def __call__(self, e, n):
        u = e * self.c - n * self.s
        v = e * self.s + n * self.c
        x = np.clip((u - self.u0) / (self.u1 - self.u0), 0, 1) * C.W
        z = np.clip(1 - (v - self.v0) / (self.v1 - self.v0), 0, 1) * C.W   # north at the top
        return x, z


class TLCReplay:
    name = "nyc-tlc"

    _cache: ClassVar[dict] = {}

    def __init__(self, path, zones=None, nrows=None):
        key = (str(Path(path).resolve()), str(zones), nrows)
        if key in TLCReplay._cache:                 # many engines (benchmark, training) share one parsed file
            self.__dict__.update(TLCReplay._cache[key])
            self.world, self.keep = None, 1.0
            return
        self._load(path, zones, nrows)
        TLCReplay._cache = {key: {k: v for k, v in self.__dict__.items() if k not in ("world", "keep")}}

    def _load(self, path, zones, nrows):
        df = load_trips(path, zones, nrows)
        if len(df) < 100:
            raise ValueError(f"Only {len(df)} usable trips found in {path}")
        proj = Projector(df)
        px, pz = proj(df.ex.values, df.ny.values)
        dx, dz = proj(df.dex.values, df.dny.values)
        order = np.argsort(df.sec.values, kind="stable")
        self.sec = df.sec.values[order].astype(float)
        self.px, self.pz, self.dx, self.dz = px[order], pz[order], dx[order], dz[order]
        self.days = max(1, df.date.nunique())
        self.n = len(df)
        self.path = str(path)
        self.label = f"NYC TLC file {Path(path).name}"
        self.frame = df.assign(x=px, z=pz, qx=dx, qz=dz)
        self.world = None
        self.keep = 1.0

    def attach(self, world):
        self.world = world
        # Size the replay so the busiest hour of the data is about 1.25x what the fleet is built for.
        self.keep = self.keep_for(world.fleet)
        scale = f"sampled at {self.keep * 100:.1f}%" if self.keep < 1 else f"scaled up {self.keep:.1f}x"
        world.log("data", f"Replaying {self.n:,} trips from {self.label} over {self.days} day(s), {scale} "
                          f"so the busiest hour is 1.25x fleet capacity")

    def keep_for(self, fleet: int) -> float:
        hourly = np.bincount((self.sec // 3600).astype(int), minlength=24) / (self.days * 3600)
        return 1.25 * fleet * C.DEMAND_PER_UNIT / max(hourly.max(), 1e-9)

    def base_rate(self) -> float:
        w = self.world
        s = w.clock() % 86400
        lo, hi = np.searchsorted(self.sec, [s - 900, s + 900])
        local = (hi - lo) / (1800 * self.days)
        return local * self.keep * w.demand_mult

    def step(self, dt):
        w = self.world
        s1 = w.clock() % 86400
        s0 = s1 - dt
        if s0 >= 0:
            idx = np.arange(*np.searchsorted(self.sec, [s0, s1]))
        else:
            a = np.arange(np.searchsorted(self.sec, s0 + 86400), self.n)
            b = np.arange(0, np.searchsorted(self.sec, s1))
            idx = np.concatenate([a, b])
        p = self.keep * w.demand_mult / self.days
        out = []
        for i in idx:
            copies = int(p) + (1 if w.rng.random() < p - int(p) else 0)
            for _ in range(copies):
                out.append((float(self.px[i]), float(self.pz[i]), float(self.dx[i]), float(self.dz[i])))
        return out

    def drift(self, dt):
        pass


def zone_minute_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """Pickups per (date, minute-of-day, zone), for training the demand forecaster on real data."""
    z = [zone_of(x, zz) for x, zz in zip(frame.x.values, frame.z.values)]
    g = pd.DataFrame({"date": frame.date.values, "minute": (frame.sec.values // 60).astype(int), "zone": z})
    return g.groupby(["date", "minute", "zone"]).size().rename("count").reset_index()
