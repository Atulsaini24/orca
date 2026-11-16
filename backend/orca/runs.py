"""Head-to-head simulation runs, executed in parallel worker processes.

Every run builds one Engine (one world, both dispatchers on identical requests, traffic,
incidents and drivers), optionally injects scripted incidents, and returns wait statistics
for both dispatchers. Workers are separate processes, so each can apply its own scoring
weights (``params``) without affecting the others.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np

REAL_DATA = None          # each city replays its own prepared trips (orca.cityspec.trips_file)
SCENARIOS = {
    "steady": [],
    "stress": [(20, "spike"), (20, "outage"), (40, "accident")],
}


@dataclass(frozen=True)
class RunSpec:
    seed: int
    minutes: int
    scenario: str = "steady"
    use_learned: bool = True
    fleet: int = 1200
    demand: str = "tlc"
    data: str | None = None             # None = the city's own prepared trips
    city: str | None = None             # None = the default city (Manhattan's streets when built)
    params: dict | None = None          # scoring weights; None = whatever config loaded (tuned if available)
    pool: bool = False                  # ORCA offers shared rides
    demand_fleet: int | None = None     # size demand for this many cars (None = fleet): fleet sweeps keep riders fixed
    start_hour: float | None = None     # simulated clock at the start; None = config.START_CLOCK
    warm: int = 300
    hourly: bool = False                # also return mean wait per clock hour
    tag: str = ""
    events: tuple = field(default=())


def _stats(d) -> dict:
    """Wait statistics since the warm-up, plus distance and sharing for the whole run."""
    w = np.asarray(d.m["all"]) if d.m["all"] else np.zeros(1)
    s = d.stats()
    return dict(mean=float(w.mean()), p50=float(np.percentile(w, 50)), p95=float(np.percentile(w, 95)),
                served=int(d.m["served"]), lost=int(d.m["lost"]), reoffers=int(d.m["reoffers"]),
                **{k: float(s[k]) for k in ("veh_km", "km_per_rider", "svc_km_per_rider", "occupancy", "shared", "detour")})


def run(spec: RunSpec) -> dict:
    from . import city as G
    from . import config as C
    G.use(spec.city)
    # Workers are reused across runs: always start from the loaded configuration, then override.
    C.apply({**C.DEFAULTS, **C.POOL_DEFAULTS, **C.TUNED, **(spec.params or {})})
    C.START_CLOCK = spec.start_hour * 3600 - spec.warm if spec.start_hour is not None else C.START_DEFAULT
    from . import cityspec
    from .engine import Engine
    data = spec.data or (str(cityspec.trips_file(G.current().name)) if cityspec.trips_file(G.current().name) else None)
    e = Engine(seed=spec.seed, fleet=spec.fleet, use_learned=spec.use_learned, demand=spec.demand if data else "synthetic",
               data=data if spec.demand == "tlc" else None, city=spec.city, pool=spec.pool, demand_fleet=spec.demand_fleet)
    e.warm_up(spec.warm)
    for d in (e.smart, e.base):
        d.m["all"].clear()
        d.m["detour"].clear()
        d.m["served"] = d.m["lost"] = d.m["reoffers"] = d.m["dropped"] = d.m["shared"] = 0
        d.f.odo[:] = 0.0
    todo = sorted(spec.events or SCENARIOS[spec.scenario])
    hourly = {"orca": {}, "nearest": {}}
    seen = {"orca": 0, "nearest": 0}
    for i in range(spec.minutes * 60):
        while todo and todo[0][0] * 60 == i:
            getattr(e.world, f"trigger_{todo.pop(0)[1]}")()
        e.step(1.0)
        if spec.hourly and i % 60 == 59:
            h = int((e.world.clock() // 3600) % 24)
            for name, d in (("orca", e.smart), ("nearest", e.base)):
                new = d.m["all"][seen[name]:]
                seen[name] = len(d.m["all"])
                hourly[name].setdefault(h, []).extend(new)
    out = dict(spec=dict(seed=spec.seed, minutes=spec.minutes, scenario=spec.scenario, use_learned=spec.use_learned, city=G.current().name,
                         params=spec.params, tag=spec.tag, start_hour=spec.start_hour, pool=spec.pool, fleet=spec.fleet,
                         demand_fleet=spec.demand_fleet))
    for name, d in (("orca", e.smart), ("nearest", e.base)):
        out[name] = _stats(d)
    if spec.hourly:
        out["hourly"] = {name: {h: dict(mean=float(np.mean(v)), n=len(v)) for h, v in sorted(hh.items()) if v}
                         for name, hh in hourly.items()}
    return out


def run_many(specs: list[RunSpec], workers: int | None = None, progress: bool = True) -> list[dict]:
    """Run specs in parallel; results come back in the order given."""
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    if workers == 1 or len(specs) == 1:
        return [run(s) for s in specs]
    out: list[dict | None] = [None] * len(specs)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run, s): i for i, s in enumerate(specs)}
        from concurrent.futures import as_completed
        for k, f in enumerate(as_completed(futs), 1):
            out[futs[f]] = f.result()
            if progress:
                s = specs[futs[f]]
                print(f"  [{k:3d}/{len(specs)}] {s.tag or s.scenario} seed {s.seed}: "
                      f"ORCA {out[futs[f]]['orca']['mean']:6.1f}s  nearest {out[futs[f]]['nearest']['mean']:6.1f}s", flush=True)
    return out  # type: ignore[return-value]


def bootstrap_ci(x: np.ndarray, stat=np.mean, n: int = 10_000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap confidence interval over seeds."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return float(x.mean()), float(x.mean())
    rng = np.random.default_rng(seed)
    boots = stat(x[rng.integers(0, len(x), (n, len(x)))], axis=1)
    return float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2))
