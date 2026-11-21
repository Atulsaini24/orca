"""Calibrate a street city's travel speeds against its real trips.

    python -m orca.calibrate --city manhattan     # writes artifacts/speed_calibration_<city>.json

For a sample of real weekday trips (07:00-21:00), route each pickup -> dropoff through the street
network on the fastest path and compute the simulated driving time the way the simulator moves
cars: the real speed of the zones it starts and ends in, at that hour, times the road-class factor.
The single factor that makes the median simulated trip as long as the median real trip becomes the
city's speed scale. Reported with the error distribution, so the fit is visible rather than assumed.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from . import city as G
from . import cityspec
from .demand import StreetProjector, load_trips
from .models import ARTIFACTS


def main(n: int = 3000, seed: int = 0, name: str = "manhattan"):
    t0 = time.time()
    city = G.use(name)
    trips = load_trips(cityspec.trips_file(name), nrows=None)
    trips = trips[(trips.sec >= 7 * 3600) & (trips.sec < 21 * 3600) & trips.dur.between(120, 3600)]
    s = trips.sample(min(n, len(trips)), random_state=seed)
    proj = StreetProjector(city)
    px, pz = proj(s.ex.values, s.ny.values)
    dx, dz = proj(s.dex.values, s.dny.values)
    raw_speed = city.base_speed * city.speed_scale                  # undo any previous calibration
    sim = np.empty(len(s))
    for i, sec in enumerate(s.sec.values):
        u, v = city.nearest_node(px[i], pz[i]), city.nearest_node(dx[i], dz[i])
        dist, _ = city.tree_to(v)                                   # free-flow weight = length / class multiplier
        local = 0.5 * (city.traffic(px[i], pz[i], sec) + city.traffic(dx[i], dz[i], sec))
        sim[i] = dist[u] / (raw_speed * local)
    real = s.dur.values
    ok = np.isfinite(sim) & (sim > 30)
    scale = float(np.median(sim[ok] / real[ok]))                    # speeds x scale -> median trip matches reality
    adj = sim[ok] / scale
    err = np.abs(adj - real[ok])
    out = dict(city=name, speed_scale=scale, trips=int(ok.sum()), median_real_s=float(np.median(real[ok])),
               median_sim_s_before=float(np.median(sim[ok])), median_sim_s_after=float(np.median(adj)),
               mae_s_after=float(err.mean()), median_abs_err_s_after=float(np.median(err)),
               corr=float(np.corrcoef(adj, real[ok])[0, 1]),
               note="Real durations include stops and signals; the simulator moves cars continuously at the calibrated speed.",
               calibrated_at=time.strftime("%Y-%m-%d %H:%M"))
    (ARTIFACTS / f"speed_calibration_{name}.json").write_text(json.dumps(out, indent=2))
    print(f"{city.label}: {ok.sum():,} real trips: median real {out['median_real_s'] / 60:.1f} min, simulated "
          f"{out['median_sim_s_before'] / 60:.1f} min before calibration -> speed scale {scale:.3f}; "
          f"after: median error {out['median_abs_err_s_after'] / 60:.1f} min, correlation {out['corr']:.2f} ({time.time() - t0:.0f}s)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="manhattan", choices=list(cityspec.CITIES))
    ap.add_argument("--trips", type=int, default=3000)
    a = ap.parse_args()
    main(a.trips, name=a.city)
