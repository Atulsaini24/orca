"""Reproducible benchmark: nearest-car vs ORCA on real NYC demand, with confidence intervals.

    python -m orca.bench                         # 10 seeds x 2 scenarios + a full 24-hour day, all cores
    python -m orca.bench --seeds 1 2 --no-day    # quick check

Every run uses identical requests, traffic, incidents and drivers for both dispatchers.
Policies compared on each seed (paired):
  nearest          the standard nearest-car rule
  orca_heuristic   ORCA with the fallback models (observed-speed ETA, EWMA demand)
  orca_learned     ORCA with the learned ETA and demand models
  orca_untuned     ORCA, learned models, hand-set scoring weights (only if tuned weights exist)

Statistics are over seeds: means with 95% percentile-bootstrap intervals, the paired
per-seed reduction against nearest-car, and Wilcoxon signed-rank p-values for the
ablations. Tuning (orca.tune) uses seeds 101+, never the benchmark's seeds.
Writes docs/benchmark.json, docs/benchmark.md and docs/benchmark.png.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon

from . import config as C
from .runs import REAL_DATA, SCENARIOS, RunSpec, bootstrap_ci, run_many

DOCS = Path(__file__).resolve().parents[2] / "docs"
LABELS = {"nearest": "Nearest car", "orca_heuristic": "ORCA, heuristic models", "orca_learned": "ORCA, learned models",
          "orca_untuned": "ORCA, learned, hand-set weights"}


def summarise(rows: list[dict], scen: str, pol: str) -> dict:
    rs = [r for r in rows if r["scenario"] == scen and pol in r]
    out = {}
    for k in ("mean", "p50", "p95", "served", "lost", "reoffers"):
        v = np.array([r[pol][k] for r in rs])
        lo, hi = bootstrap_ci(v)
        out[k] = dict(value=float(v.mean()), ci=[lo, hi])
    if pol != "nearest":
        red = np.array([1 - r[pol]["mean"] / r["nearest"]["mean"] for r in rs])
        lo, hi = bootstrap_ci(red)
        out["reduction_vs_nearest"] = dict(value=float(red.mean()), ci=[lo, hi])
    return out


def paired_p(rows, scen, a, b) -> float | None:
    rs = [r for r in rows if r["scenario"] == scen and a in r and b in r]
    d = np.array([r[a]["mean"] - r[b]["mean"] for r in rs])
    if len(d) < 5 or np.allclose(d, 0):
        return None
    return float(wilcoxon(d).pvalue)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=int, default=45)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 11)))
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--demand", choices=["tlc", "synthetic"], default="tlc")
    ap.add_argument("--data", default=str(REAL_DATA))
    ap.add_argument("--day-seeds", type=int, nargs="*", default=[1, 2], help="seeds for the full 24-hour run")
    ap.add_argument("--no-day", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    t0 = time.time()
    tuned = bool(C.TUNED) and C.TUNED != C.DEFAULTS
    base = dict(minutes=a.minutes, fleet=a.fleet, demand=a.demand, data=a.data)
    specs: list[RunSpec] = []
    for scen in SCENARIOS:
        for s in a.seeds:
            specs.append(RunSpec(seed=s, scenario=scen, use_learned=True, tag=f"{scen}/learned", **base))
            specs.append(RunSpec(seed=s, scenario=scen, use_learned=False, tag=f"{scen}/heuristic", **base))
            if tuned:
                specs.append(RunSpec(seed=s, scenario=scen, use_learned=True, params=dict(C.DEFAULTS), tag=f"{scen}/untuned", **base))
    day = [] if a.no_day else [RunSpec(seed=s, minutes=24 * 60, start_hour=0.0, hourly=True, tag="day/learned",
                                       fleet=a.fleet, demand=a.demand, data=a.data) for s in a.day_seeds]
    print(f"{len(specs)} benchmark runs + {len(day)} full-day runs on {a.workers or 'all-but-one'} cores", flush=True)
    res = run_many(day + specs, workers=a.workers)
    day_res, res = res[: len(day)], res[len(day):]

    rows: dict[tuple, dict] = {}
    for spec, r in zip(specs, res):
        row = rows.setdefault((spec.scenario, spec.seed), dict(scenario=spec.scenario, seed=spec.seed))
        kind = spec.tag.split("/")[1]
        row[{"learned": "orca_learned", "heuristic": "orca_heuristic", "untuned": "orca_untuned"}[kind]] = r["orca"]
        if kind == "learned":
            row["nearest"] = r["nearest"]
    rows_l = sorted(rows.values(), key=lambda r: (r["scenario"], r["seed"]))
    pols = ["nearest", "orca_heuristic", "orca_learned"] + (["orca_untuned"] if tuned else [])
    summary = {scen: {p: summarise(rows_l, scen, p) for p in pols} for scen in SCENARIOS}
    tests = {scen: dict(learned_vs_heuristic=paired_p(rows_l, scen, "orca_learned", "orca_heuristic"),
                        tuned_vs_untuned=paired_p(rows_l, scen, "orca_learned", "orca_untuned") if tuned else None,
                        learned_vs_nearest=paired_p(rows_l, scen, "orca_learned", "nearest")) for scen in SCENARIOS}
    full_day = None
    if day_res:
        hours = range(24)
        def curve(name):
            out = []
            for h in hours:
                vals = [r["hourly"][name].get(h) or r["hourly"][name].get(str(h)) for r in day_res]
                vals = [v for v in vals if v]
                out.append(float(np.average([v["mean"] for v in vals], weights=[v["n"] for v in vals])) if vals else None)
            return out
        full_day = dict(seeds=a.day_seeds, orca=curve("orca"), nearest=curve("nearest"),
                        totals={k: dict(mean=float(np.mean([r[k]["mean"] for r in day_res])),
                                        served=int(np.mean([r[k]["served"] for r in day_res])),
                                        lost=int(np.mean([r[k]["lost"] for r in day_res]))) for k in ("orca", "nearest")})

    out = dict(config=dict(minutes=a.minutes, seeds=a.seeds, fleet=a.fleet, demand=a.demand, tuned_weights=tuned,
                           weights=dict(C.TUNED) if tuned else dict(C.DEFAULTS)),
               rows=rows_l, summary=summary, tests=tests, full_day=full_day, seconds=round(time.time() - t0))
    DOCS.mkdir(exist_ok=True)
    (DOCS / "benchmark.json").write_text(json.dumps(out, indent=2))
    write_md(out, pols)
    plot(out, pols)
    print(f"Wrote docs/benchmark.json, .md, .png ({time.time() - t0:.0f}s)")


def fmt_ci(d, unit="s", pct=False):
    if pct:
        return f"{d['value'] * 100:.0f}% ({d['ci'][0] * 100:.0f}-{d['ci'][1] * 100:.0f}%)"
    return f"{d['value']:.0f}{unit} ({d['ci'][0]:.0f}-{d['ci'][1]:.0f})"


def fmt_p(p):
    return "n/a" if p is None else ("< 0.001" if p < 0.001 else f"{p:.3f}")


def write_md(out, pols):
    c = out["config"]
    md = ["# Benchmark", "",
          f"{c['minutes']} simulated minutes per run after a 5-minute warm-up, {c['fleet']} cars per dispatcher, "
          f"{len(c['seeds'])} seeds ({c['seeds'][0]}-{c['seeds'][-1]}). Both dispatchers see identical requests, traffic, "
          "incidents and drivers on every seed.",
          ("Demand replays real NYC TLC yellow-taxi trips (January 2015 Manhattan weekdays) at the real hourly rate; "
           "citywide road speed follows the real median taxi speed for each hour." if c["demand"] == "tlc"
           else "Demand comes from the procedural demand model."),
          "The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.",
          f"ORCA's scoring weights are {'tuned by `orca.tune` on seeds 101-109, which the benchmark never uses' if c['tuned_weights'] else 'the hand-set defaults'}.",
          "", "Values are means over seeds with 95% bootstrap confidence intervals in brackets.", ""]
    for scen, title in (("steady", "Steady demand"), ("stress", "Spike, GPS outage and accident")):
        S = out["summary"][scen]
        md += [f"## {title}", "", "| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |",
               "|---|---:|---:|---:|---:|---:|"]
        for p in pols:
            s = S[p]
            md.append(f"| {LABELS[p]} | {fmt_ci(s['mean'])} | {fmt_ci(s['p95'])} | {s['lost']['value']:.1f} | "
                      f"{s['reoffers']['value']:.0f} | {fmt_ci(s['reduction_vs_nearest'], pct=True) if 'reduction_vs_nearest' in s else '–'} |")
        t = out["tests"][scen]
        md += ["", f"Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = {fmt_p(t['learned_vs_heuristic'])}"
                   + (f"; tuned vs hand-set weights p = {fmt_p(t['tuned_vs_untuned'])}" if t["tuned_vs_untuned"] is not None else "")
                   + f"; ORCA vs nearest p = {fmt_p(t['learned_vs_nearest'])}.", ""]
    fd = out["full_day"]
    if fd:
        o, n = fd["totals"]["orca"], fd["totals"]["nearest"]
        md += ["## A full day", "",
               f"24 simulated hours from midnight on the real demand and traffic profile (seeds {', '.join(map(str, fd['seeds']))}): "
               f"mean wait {o['mean']:.0f}s with ORCA vs {n['mean']:.0f}s with nearest car "
               f"({(1 - o['mean'] / n['mean']) * 100:.0f}% less); riders lost {o['lost']} vs {n['lost']}.", "",
               "| Hour | " + " | ".join(f"{h:02d}" for h in range(0, 24, 3)) + " |", "|---|" + "---:|" * 8,
               "| ORCA | " + " | ".join(f"{fd['orca'][h]:.0f}s" if fd["orca"][h] else "–" for h in range(0, 24, 3)) + " |",
               "| Nearest | " + " | ".join(f"{fd['nearest'][h]:.0f}s" if fd["nearest"][h] else "–" for h in range(0, 24, 3)) + " |", ""]
    (DOCS / "benchmark.md").write_text("\n".join(md))


def plot(out, pols):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # plotting is optional
        print("chart skipped:", exc)
        return
    fd = out["full_day"]
    fig, axes = plt.subplots(1, 3 if fd else 2, figsize=(15 if fd else 10, 3.8), gridspec_kw=dict(width_ratios=[1, 1, 1.4] if fd else [1, 1]))
    colors = {"nearest": "#8A9099", "orca_heuristic": "#E8B400", "orca_learned": "#1D1F23", "orca_untuned": "#C9CDD3"}
    for ax, scen in zip(axes, ("steady", "stress")):
        S = out["summary"][scen]
        xs = np.arange(len(pols))
        vals = [S[p]["mean"]["value"] for p in pols]
        err = np.array([[S[p]["mean"]["value"] - S[p]["mean"]["ci"][0], S[p]["mean"]["ci"][1] - S[p]["mean"]["value"]] for p in pols]).T
        ax.bar(xs, vals, 0.62, color=[colors[p] for p in pols], yerr=err, capsize=4, ecolor="#555")
        ax.set_xticks(xs, [LABELS[p].replace("ORCA, ", "ORCA\n").replace("learned, ", "learned\n") for p in pols], fontsize=8)
        ax.set_title(f"{scen.capitalize()}: mean wait, 95% CI", loc="left", fontsize=11)
        ax.set_ylabel("seconds")
        ax.spines[["top", "right"]].set_visible(False)
    if fd:
        ax = axes[2]
        h = np.arange(24)
        for name, col, lab in (("nearest", "#8A9099", "Nearest car"), ("orca", "#1D1F23", "ORCA")):
            ax.plot(h, [v if v is not None else np.nan for v in fd[name]], color=col, lw=2.2, label=lab, marker="o", ms=3)
        ax.set_xticks(range(0, 24, 3), [f"{x:02d}:00" for x in range(0, 24, 3)], fontsize=8)
        ax.set_title("A full day: mean wait by hour", loc="left", fontsize=11)
        ax.set_ylabel("seconds")
        ax.legend(frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(DOCS / "benchmark.png", dpi=160)


if __name__ == "__main__":
    main()
