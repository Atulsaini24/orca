"""Train and evaluate ORCA's learned models.

    python -m orca.train eta                         # ETA model from fleet telemetry under real NYC demand and traffic
    python -m orca.train demand                      # demand forecaster on real NYC TLC pickups
    python -m orca.train demand --source sim         # same, on the procedural demand model
    python -m orca.train eta-real                    # trip-duration model trained and tested on real NYC trips

Every model is compared against the simpler baselines it replaces, on a
time-based split (train on earlier data, test on later data, never shuffled).
Artifacts and metrics are written to ``artifacts/``.
"""
from __future__ import annotations

import argparse
import json
import random
import time

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from . import config as C
from .features import DEMAND_FEATURES, ETA_FEATURES, FeatureStore
from .models import ARTIFACTS

REAL_DATA = ARTIFACTS.parent / "data" / "nyc_tlc_2015-01_manhattan.parquet"

N_ZONES = C.ZONES * C.ZONES


def _report(name, y, pred):
    err = np.abs(y - pred)
    return dict(model=name, mae=float(mean_absolute_error(y, pred)), p90_abs_err=float(np.percentile(err, 90)),
                r2=float(r2_score(y, pred)))


def _print(title, rows, unit):
    print(f"\n{title}")
    print(f"  {'model':<34}{'MAE':>10}{'p90 |err|':>12}{'R²':>8}")
    for r in rows:
        print(f"  {r['model']:<34}{r['mae']:>9.2f}{unit}{r['p90_abs_err']:>11.2f}{unit}{r['r2']:>8.3f}")


# ---------------------------------------------------------------------------- ETA
def _eta_run(job: tuple) -> np.ndarray:
    """One telemetry run (worker process): fleet under real demand from a given hour, frequent incidents."""
    seed, start_hour, hours, fleet, source, data, incident_every = job
    if start_hour is not None:
        C.START_CLOCK = start_hour * 3600
    from .engine import Engine
    e = Engine(seed=seed, fleet=fleet, use_learned=False, telemetry=True,
               demand="tlc" if source == "tlc" else "synthetic", data=data)
    rng = random.Random(seed)
    for i in range(int(hours * 3600)):
        e.step(1.0)
        if i > 600 and rng.random() < 1 / incident_every:
            rng.choice([e.world.trigger_spike, e.world.trigger_outage, e.world.trigger_accident])()
    return np.array(e.telemetry, dtype=float)


def collect_eta(hours: float, seeds: list[int], fleet: int, source: str = "tlc", data: str | None = None,
                start_hours: list[float] | None = None, incident_every: float = 700) -> list[np.ndarray]:
    """Telemetry runs in parallel, each starting at a different time of day (real traffic varies by hour)."""
    from concurrent.futures import ProcessPoolExecutor
    starts = start_hours or [None] * len(seeds)
    jobs = [(s, h, hours, fleet, source, data, incident_every) for s, h in zip(seeds, starts)]
    with ProcessPoolExecutor(max_workers=min(len(jobs), 7)) as ex:
        runs = list(ex.map(_eta_run, jobs))
    for (s, h, *_), r in zip(jobs, runs):
        print(f"  seed {s}" + (f" from {h:05.2f}h" if h is not None else "") + f": {len(r):,} pickup legs logged")
    return runs


def physics_eta(X: np.ndarray) -> np.ndarray:
    """Manhattan distance over the observed zone speeds along the route, plus a fixed pickup overhead."""
    return X[:, 0] / np.maximum(0.05, X[:, 3:6].mean(axis=1)) + 4


def train_eta(args):
    t0 = time.time()
    starts = [float(h) for h in args.start_hours] if args.start_hours else None
    seeds = args.seeds if not starts else list(range(1, len(starts) + 1))
    print(f"Collecting fleet telemetry: {args.hours} sim-hours x {len(seeds)} runs"
          + (f" starting at {', '.join(f'{h:g}h' for h in starts)}" if starts else "") + f", {args.fleet} cars, incidents ~every "
          f"{args.incident_every / 60:.0f} min")
    runs = collect_eta(args.hours, seeds, args.fleet, args.source, args.data, starts, args.incident_every)
    tr, te = [], []
    for r in runs:                                   # time split inside every run: earlier 80% train, later 20% test
        r = r[r[:, -1] > 0]
        cut = int(len(r) * 0.8)
        tr.append(r[:cut])
        te.append(r[cut:])
    tr, te = np.concatenate(tr), np.concatenate(te)
    Xtr, ytr, Xte, yte = tr[:, :-1], tr[:, -1], te[:, :-1], te[:, -1]

    manh = Xte[:, 0]
    free_flow = manh / C.BASE_SPEED + 4
    obs = physics_eta(Xte)
    # Learn a multiplicative correction to the physics baseline rather than raw seconds: trees cannot
    # extrapolate, and real traffic in the test window (later in the morning) is slower than in training.
    model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=500, learning_rate=0.05, max_leaf_nodes=31,
                                          l2_regularization=1.0, early_stopping=True, random_state=0)
    model.fit(Xtr, np.log(ytr / physics_eta(Xtr)))
    gbm = np.maximum(4.0, obs * np.exp(model.predict(Xte)))
    rows = [_report("free-flow distance / speed", yte, free_flow),
            _report("observed zone-speed heuristic", yte, obs),
            _report("gradient boosting (ORCA)", yte, gbm)]
    _print(f"Pickup ETA on {len(yte):,} held-out legs (later in time than training)", rows, "s")
    ARTIFACTS.mkdir(exist_ok=True)
    meta = dict(task="eta", target="log_ratio_to_physics", source=args.source, features=ETA_FEATURES, n_train=len(ytr), n_test=len(yte), results=rows,
                hours=args.hours, seeds=seeds, start_hours=starts, incident_every=args.incident_every, fleet=args.fleet, trained_at=time.strftime("%Y-%m-%d %H:%M"))
    joblib.dump(dict(model=model, meta=meta), ARTIFACTS / "eta.joblib")
    (ARTIFACTS / "eta_metrics.json").write_text(json.dumps(meta, indent=2))
    print(f"\nSaved artifacts/eta.joblib  ({time.time() - t0:.0f}s)")


# ---------------------------------------------------------------------------- demand
def sim_counts(hours: float, seeds: list[int], fleet: int) -> list[tuple[np.ndarray, float]]:
    """Per-minute pickup counts per zone from the synthetic demand model (no dispatch needed)."""
    from .world import World
    out = []
    for seed in seeds:
        w = World(seed, fleet)
        rng = random.Random(seed + 99)
        minutes = int(hours * 60)
        M = np.zeros((minutes, N_ZONES))
        start_clock = w.clock()
        for m in range(minutes):
            for _ in range(60):
                for r in w.step(1.0):
                    M[m, r.zone] += 1
            if rng.random() < 1 / 45:
                w.trigger_spike()
        out.append((M, start_clock))
        print(f"  seed {seed}: {int(M.sum()):,} requests over {minutes} minutes")
    return out


def tlc_counts(path, zones, fleet) -> list[tuple[np.ndarray, float]]:
    from .demand import TLCReplay, zone_minute_counts
    rep = TLCReplay(path, zones)
    g = zone_minute_counts(rep.frame)
    keep = rep.keep_for(fleet)
    out = []
    for _date, day in g.groupby("date"):
        M = np.zeros((1440, N_ZONES))
        M[day.minute.values, day.zone.values] = day["count"].values
        out.append((M * keep, 0.0))         # same scaling the live replay applies
    print(f"  {len(out)} day(s) of real pickups, {int(sum(m.sum() for m, _ in out)):,} trips")
    return out


def demand_dataset(series: list[tuple[np.ndarray, float]], horizon: int = 5):
    X, y, ewma, naive = [], [], [], []
    for M, start_clock in series:
        fs = FeatureStore()
        rate, prev, init = np.zeros(N_ZONES), np.zeros(N_ZONES), False
        for m in range(len(M) - horizon):
            fs.minute = list(M[m])
            fs.roll_minute()
            prev, rate = rate.copy(), (0.65 * rate + 0.35 * M[m]) if init else M[m].copy()
            init = True
            if m < 15:
                continue
            clock = start_clock + (m + 1) * 60
            target = M[m + 1:m + 1 + horizon].sum(axis=0)
            trend = np.clip(rate / (prev + 1e-3), 0.7, 1.5)
            for z in range(N_ZONES):
                X.append(fs.demand_features(z, clock))
                y.append(target[z])
                ewma.append(rate[z] * horizon * trend[z])
                naive.append(M[m, z] * horizon)
    return np.array(X), np.array(y), np.array(ewma), np.array(naive)


def train_demand(args):
    t0 = time.time()
    if args.source == "tlc":
        print(f"Building demand series from real trips in {args.data}")
        series = tlc_counts(args.data, args.zones, args.fleet)
    else:
        print(f"Building demand series: {args.hours} sim-hours x {len(args.seeds)} seeds")
        series = sim_counts(args.hours, args.seeds, args.fleet)
    X, y, ewma, naive = demand_dataset(series)
    cut = int(len(y) * 0.8)
    model = HistGradientBoostingRegressor(loss="poisson", max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                                          early_stopping=True, random_state=0)
    model.fit(X[:cut], y[:cut])
    pred = np.maximum(0, model.predict(X[cut:]))
    yt = y[cut:]
    rows = [_report("last minute x 5", yt, naive[cut:]),
            _report("EWMA with trend", yt, ewma[cut:]),
            _report("gradient boosting, Poisson (ORCA)", yt, pred)]
    _print(f"Requests per zone in the next 5 min, {len(yt):,} held-out zone-minutes", rows, " ")
    ARTIFACTS.mkdir(exist_ok=True)
    meta = dict(task="demand", source=args.source, features=DEMAND_FEATURES, n_train=int(cut), n_test=len(yt),
                results=rows, trained_at=time.strftime("%Y-%m-%d %H:%M"))
    joblib.dump(dict(model=model, meta=meta), ARTIFACTS / f"demand_{args.source}.joblib")
    (ARTIFACTS / f"demand_{args.source}_metrics.json").write_text(json.dumps(meta, indent=2))
    print(f"\nSaved artifacts/demand_{args.source}.joblib  ({time.time() - t0:.0f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=["eta", "demand", "eta-real"])
    ap.add_argument("--source", choices=["sim", "tlc"], default="tlc")
    ap.add_argument("--data", default=str(REAL_DATA), help="NYC TLC trip file (CSV or Parquet) when --source tlc")
    ap.add_argument("--zones", help="zone centroid CSV for PULocationID-style files")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--start-hours", type=float, nargs="*", default=[0, 4, 7.5, 12, 16.5, 20],
                    help="ETA: one telemetry run starting at each of these clock hours")
    ap.add_argument("--incident-every", type=float, default=700, help="ETA: mean seconds between random incidents")
    args = ap.parse_args()
    if args.task == "eta-real":
        from .real_eta import train as train_eta_real
        return train_eta_real(args)
    (train_eta if args.task == "eta" else train_demand)(args)


if __name__ == "__main__":
    main()
