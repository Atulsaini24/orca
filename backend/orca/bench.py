"""Reproducible benchmark: nearest-unit vs ORCA, with an ablation of the learned models.

    python -m orca.bench --minutes 60 --seeds 1 2

Every run uses identical requests, traffic and incidents for all policies.
Writes docs/benchmark.json, docs/benchmark.md and docs/benchmark.png.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .engine import Engine

DOCS = Path(__file__).resolve().parents[2] / "docs"
SCENARIOS = {
    "steady": [],
    "stress": [(20, "spike"), (20, "outage"), (40, "accident")],
}


REAL_DATA = Path(__file__).resolve().parents[1] / "data" / "nyc_tlc_2015-01_manhattan.parquet"


def run(seed, minutes, events, use_learned, fleet, demand="tlc", data=None):
    e = Engine(seed=seed, fleet=fleet, use_learned=use_learned, demand=demand, data=data)
    e.warm_up(300)
    for d in (e.smart, e.base):
        d.m["all"].clear()
        d.m["served"] = d.m["lost"] = d.m["reoffers"] = 0
    todo = sorted(events)
    for i in range(minutes * 60):
        while todo and todo[0][0] * 60 == i:
            getattr(e.world, f"trigger_{todo.pop(0)[1]}")()
        e.step(1.0)
    out = {}
    for name, d in (("orca", e.smart), ("nearest", e.base)):
        w = np.array(d.m["all"]) if d.m["all"] else np.zeros(1)
        out[name] = dict(mean=float(w.mean()), p50=float(np.percentile(w, 50)), p95=float(np.percentile(w, 95)),
                         served=d.m["served"], lost=d.m["lost"], reoffers=d.m["reoffers"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--demand", choices=["tlc", "synthetic"], default="tlc")
    ap.add_argument("--data", default=str(REAL_DATA))
    a = ap.parse_args()
    t0 = time.time()
    rows = []
    for scen, events in SCENARIOS.items():
        for seed in a.seeds:
            learned = run(seed, a.minutes, events, True, a.fleet, a.demand, a.data)
            plain = run(seed, a.minutes, events, False, a.fleet, a.demand, a.data)
            rows.append(dict(scenario=scen, seed=seed, nearest=learned["nearest"], orca_heuristic=plain["orca"],
                             orca_learned=learned["orca"]))
            print(f"{scen:7s} seed {seed}: mean wait nearest {learned['nearest']['mean']:6.1f}s  "
                  f"ORCA heuristic {plain['orca']['mean']:6.1f}s  ORCA learned {learned['orca']['mean']:6.1f}s")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "benchmark.json").write_text(json.dumps(dict(config=vars(a), rows=rows), indent=2))

    pols = [("nearest", "Nearest unit"), ("orca_heuristic", "ORCA, heuristic models"), ("orca_learned", "ORCA, learned models")]
    agg = lambda scen, pol, k: float(np.mean([r[pol][k] for r in rows if r["scenario"] == scen]))
    md = ["# Benchmark", "",
          f"{a.minutes} simulated minutes per run after a 5-minute warm-up, {a.fleet} units per policy, "
          f"seeds {', '.join(map(str, a.seeds))}. All policies see identical requests, traffic and incidents.",
          ("Demand replays real NYC TLC yellow-taxi trips (January 2015 weekdays, Manhattan) at the real hourly rate; "
           "citywide road speed follows the real median taxi speed for each hour." if a.demand == "tlc" else
           "Demand comes from the procedural demand model."),
          "The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.", ""]
    for scen in SCENARIOS:
        md += [f"## {scen.capitalize()}", "", "| Policy | Mean wait | p95 wait | Served | Lost | Re-offers |", "|---|---:|---:|---:|---:|---:|"]
        for pol, label in pols:
            md.append(f"| {label} | {agg(scen, pol, 'mean'):.0f}s | {agg(scen, pol, 'p95'):.0f}s | {agg(scen, pol, 'served'):.0f} "
                      f"| {agg(scen, pol, 'lost'):.0f} | {agg(scen, pol, 'reoffers'):.0f} |")
        base, best = agg(scen, "nearest", "mean"), agg(scen, "orca_learned", "mean")
        md += ["", f"ORCA with learned models cuts mean wait by {(1 - best / base) * 100:.0f}% against nearest-unit dispatch.", ""]
    (DOCS / "benchmark.md").write_text("\n".join(md))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=False)
        colors = ["#8A9099", "#E8B400", "#1D1F23"]
        for ax, scen in zip(axes, SCENARIOS):
            vals = [agg(scen, p, "mean") for p, _ in pols]
            p95 = [agg(scen, p, "p95") for p, _ in pols]
            xs = np.arange(len(pols))
            ax.bar(xs - 0.18, vals, 0.36, color=colors, label="mean")
            ax.bar(xs + 0.18, p95, 0.36, color=colors, alpha=0.4, label="p95")
            ax.set_xticks(xs, ["Nearest", "ORCA\nheuristic", "ORCA\nlearned"])
            ax.set_title(scen.capitalize(), loc="left", fontsize=11)
            ax.set_ylabel("seconds from request to pickup")
            ax.spines[["top", "right"]].set_visible(False)
        axes[0].legend(frameon=False)
        fig.tight_layout()
        fig.savefig(DOCS / "benchmark.png", dpi=160)
    except Exception as exc:  # plotting is optional
        print("chart skipped:", exc)
    print(f"Wrote docs/benchmark.md ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
