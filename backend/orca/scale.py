"""How fast and how far the engine scales: simulation speed and dispatch throughput by fleet size.

    python -m orca.scale --city manhattan                       # 1,200 to 20,000 cars per dispatcher
    python -m orca.scale --fleets 1200 10000 --seconds 300

For each fleet size, on real demand sized for that fleet (one engine: ORCA and nearest-car side by side,
2 x fleet cars in all): wall time per simulated second, the speed-up over real time, ORCA's batch
decision latency (candidate search, learned ETA, acceptance, zone costs and the exact assignment) and
decisions per second of decision time, and the cost of one live frame. Then the assignment solver
alone on synthetic batches up to 5,000 requests: dense Hungarian against sparse LAPJV.

Single process, single thread (BLAS limited to one thread, as in the server). Writes docs/scale.json, .md, .png.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

DOCS = Path(__file__).resolve().parents[2] / "docs"
# The previous engine (one Python object per car, v0.3), measured with this script's method on the
# same laptop before the rewrite: wall ms per simulated second, and ms per live frame.
PREVIOUS = {1200: (14.7, 2.11), 5000: (78.4, 13.8), 10000: (139.1, 17.2)}


def measure(city: str, fleet: int, seconds: int, warm: int) -> dict:
    from . import cityspec
    from .engine import Engine
    from .server import dumps
    data = cityspec.trips_file(city)
    e = Engine(seed=11, fleet=fleet, demand="tlc" if data else "synthetic", data=str(data) if data else None, city=city)
    e.warm_up(warm)
    d = e.smart
    d.batch_log.clear()
    dec0 = d.decisions
    t0 = time.perf_counter()
    for _ in range(seconds):
        e.step(1.0)
    wall = time.perf_counter() - t0
    log = np.array(d.batch_log, dtype=float).reshape(-1, 2)
    t0 = time.perf_counter()
    for _ in range(20):
        payload = dumps(e.frame("smart"))
    frame_ms = (time.perf_counter() - t0) / 20 * 1000
    decided = d.decisions - dec0
    return dict(fleet=fleet, ms_per_sim_s=wall / seconds * 1000, realtime=seconds / wall,
                batch_ms_mean=float(log[:, 1].mean()) if len(log) else 0.0,
                batch_ms_p95=float(np.percentile(log[:, 1], 95)) if len(log) else 0.0,
                batch_requests_mean=float(log[:, 0].mean()) if len(log) else 0.0,
                batch_requests_max=int(log[:, 0].max()) if len(log) else 0,
                decisions=int(decided), decisions_per_s=decided / (log[:, 1].sum() / 1000) if len(log) and log[:, 1].sum() else 0.0,
                frame_ms=frame_ms, frame_kb=len(payload) / 1024, served=d.m["served"], waiting=d.stats()["waiting"])


def solver_bench(sizes=(100, 500, 1000, 2000, 5000), k=10, fleet_per_request=8) -> list[dict]:
    """Exact assignment on random sparse batches: k candidate cars per request, dense vs sparse."""
    from . import config as C
    from .dispatch import OrcaDispatcher
    d = OrcaDispatcher.__new__(OrcaDispatcher)
    rng = np.random.default_rng(0)
    out = []
    saved = C.HUNGARIAN_MAX
    try:
        for nq in sizes:
            nc = nq * fleet_per_request
            qi = np.repeat(np.arange(nq), k)
            ci = rng.integers(0, nc, nq * k)
            _, u = np.unique(qi * nc + ci, return_index=True)
            qi, ci = qi[u], ci[u]
            adj = rng.uniform(5, 300, len(qi))
            row = dict(requests=nq, pairs=len(qi))
            for name, cap in (("dense", 10 ** 9), ("sparse", -1)):
                if name == "dense" and nq > 2000:
                    row[name] = None
                    continue
                C.HUNGARIAN_MAX = cap
                best = min(_timed(d.solve, qi, ci, adj, nq) for _ in range(3))
                row[name] = best * 1000
            out.append(row)
    finally:
        C.HUNGARIAN_MAX = saved
    return out


def _timed(f, *a):
    t0 = time.perf_counter()
    f(*a)
    return time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=None)
    ap.add_argument("--fleets", type=int, nargs="+", default=[1200, 2500, 5000, 10000, 20000])
    ap.add_argument("--seconds", type=int, default=600, help="simulated seconds timed per fleet size")
    ap.add_argument("--warm", type=int, default=300)
    a = ap.parse_args()
    from . import city as G
    city = G.use(a.city).name
    rows = []
    for fl in a.fleets:
        r = measure(city, fl, a.seconds, a.warm)
        rows.append(r)
        print(f"{fl:6d} cars: {r['ms_per_sim_s']:6.2f} ms per sim-second (x{r['realtime']:.0f} real time), "
              f"batch {r['batch_ms_mean']:.1f} ms mean / {r['batch_ms_p95']:.1f} p95 for {r['batch_requests_mean']:.0f} requests, "
              f"{r['decisions_per_s']:,.0f} decisions/s, frame {r['frame_ms']:.2f} ms ({r['frame_kb']:.0f} KB)", flush=True)
    solver = solver_bench()
    for s in solver:
        print(f"solver {s['requests']:5d} requests: dense {s['dense'] if s['dense'] is None else round(s['dense'], 1)} ms, "
              f"sparse {s['sparse']:.1f} ms", flush=True)
    out = dict(city=city, machine=dict(cpu=platform.processor(), python=platform.python_version(), os=platform.platform()),
               seconds=a.seconds, fleets=rows, solver=solver, previous={str(k): v for k, v in PREVIOUS.items()})
    DOCS.mkdir(exist_ok=True)
    (DOCS / "scale.json").write_text(json.dumps(out, indent=2))
    write_md(out)
    plot(out)
    print("Wrote docs/scale.json, .md, .png")


def write_md(out):
    md = ["# Scale", "",
          f"{out['city'].capitalize()}, real demand sized for each fleet; ORCA and nearest-car run side by side on the same "
          f"requests, so the engine moves twice the fleet. {out['seconds']} simulated seconds timed after a warm-up, one "
          f"process, one thread ({out['machine']['cpu'] or out['machine']['os']}).", "",
          "| Cars per dispatcher | Wall time per simulated second | Faster than real time | Previous engine | ORCA batch: requests, mean / p95 latency | Decisions per second | Live frame |",
          "|---:|---:|---:|---:|---:|---:|---:|"]
    for r in out["fleets"]:
        prev = out["previous"].get(str(r["fleet"]))
        ms = r["ms_per_sim_s"]
        before = f"{prev[0]:.1f} ms ({prev[0] / ms:.1f}x faster now)" if prev else "–"
        md.append(f"| {r['fleet']:,} | {ms:.1f} ms | {r['realtime']:.0f}x | {before} | "
                  f"{r['batch_requests_mean']:.0f}, {r['batch_ms_mean']:.1f} / {r['batch_ms_p95']:.1f} ms | {r['decisions_per_s']:,.0f} | "
                  f"{r['frame_ms']:.1f} ms, {r['frame_kb']:.0f} KB |")
    md += ["", "\"Previous engine\" is the object-per-car engine this project used before (v0.3), timed the same way on the same "
               "machine. A decision is one request assigned; decisions per second divide them by the time spent deciding "
               "(candidate search, learned ETA model, acceptance and zone costs, the exact assignment).", "",
           "## Assignment solver", "", "Random sparse batches, ten candidate cars per request, best of three.", "",
           "| Requests in the batch | Candidate pairs | Dense Hungarian | Sparse LAPJV |", "|---:|---:|---:|---:|"]
    for s in out["solver"]:
        dense = "–" if s["dense"] is None else f"{s['dense']:.1f} ms"
        md.append(f"| {s['requests']:,} | {s['pairs']:,} | {dense} | {s['sparse']:.1f} ms |")
    md += ["", "ORCA uses the dense method up to 150 waiting requests and the sparse one above; both are exact."]
    (DOCS / "scale.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")


def plot(out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print("chart skipped:", exc)
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    fl = [r["fleet"] for r in out["fleets"]]
    ax = axes[0]
    ax.plot(fl, [r["ms_per_sim_s"] for r in out["fleets"]], color="#E8B400", lw=2.4, marker="o", label="this engine")
    prev = sorted((int(k), v[0]) for k, v in out["previous"].items())
    ax.plot([p[0] for p in prev], [p[1] for p in prev], color="#8A9099", lw=2, marker="o", ls="--", label="previous engine")
    ax.axhline(50, color="#1D1F23", lw=1, ls=":")
    ax.text(fl[0], 52, "live view at 20x needs < 50 ms", fontsize=8)
    ax.set_xlabel("cars per dispatcher (x2 simulated)")
    ax.set_ylabel("wall ms per simulated second")
    ax.set_title("Simulation cost by fleet size", loc="left", fontsize=11)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax = axes[1]
    s = out["solver"]
    ax.plot([r["requests"] for r in s], [r["sparse"] for r in s], color="#E8B400", lw=2.4, marker="o", label="sparse LAPJV")
    dn = [r for r in s if r["dense"] is not None]
    ax.plot([r["requests"] for r in dn], [r["dense"] for r in dn], color="#1D1F23", lw=2, marker="o", label="dense Hungarian")
    ax.set_xlabel("requests in one batch")
    ax.set_ylabel("ms")
    ax.set_title("Exact assignment time", loc="left", fontsize=11)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(DOCS / "scale.png", dpi=160)


if __name__ == "__main__":
    main()
