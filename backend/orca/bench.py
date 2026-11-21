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
from .runs import SCENARIOS, RunSpec, bootstrap_ci, run_many

DOCS = Path(__file__).resolve().parents[2] / "docs"
LABELS = {"nearest": "Nearest car", "batched_eta": "Batched ETA matching (industry baseline)", "orca_heuristic": "ORCA, heuristic models", "orca_learned": "ORCA, learned models",
          "orca_untuned": "ORCA, learned, hand-set weights", "orca_no_guard": "ORCA, learned, no give-up guard",
          "orca_matching_only": "ORCA, matching only (no repositioning)"}


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


def paired_p_lost(rows, scen, a, b) -> float | None:
    """Wilcoxon signed-rank test on riders lost per seed, policy a vs b."""
    rs = [r for r in rows if r["scenario"] == scen and a in r and b in r]
    d = np.array([r[a]["lost"] - r[b]["lost"] for r in rs])
    if len(d) < 5 or np.allclose(d, 0):
        return None
    return float(wilcoxon(d).pvalue)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=int, default=45)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 11)))
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--demand", choices=["tlc", "synthetic"], default="tlc")
    ap.add_argument("--data", default=None, help="trip file (default: the city's own)")
    ap.add_argument("--city", default=None, help="city to benchmark (default: the first built real city)")
    ap.add_argument("--day-seeds", type=int, nargs="*", default=[1, 2], help="seeds for the full 24-hour run")
    ap.add_argument("--no-day", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--vs-industry", action="store_true",
                    help="only ORCA vs batched ETA matching vs nearest car; writes docs/industry_<city>.md")
    a = ap.parse_args()
    t0 = time.time()
    from . import city as G
    city = G.use(a.city)
    if a.vs_industry:
        return industry(a, city, t0)
    tuned = bool(C.TUNED) and C.TUNED != C.DEFAULTS
    # every worker process must run the same city as this one: pass it explicitly
    base = dict(minutes=a.minutes, fleet=a.fleet, demand=a.demand, data=a.data, city=city.name)
    specs: list[RunSpec] = []
    for scen in SCENARIOS:
        for s in a.seeds:
            specs.append(RunSpec(seed=s, scenario=scen, use_learned=True, tag=f"{scen}/learned", **base))
            specs.append(RunSpec(seed=s, scenario=scen, use_learned=False, tag=f"{scen}/heuristic", **base))
            if tuned:
                specs.append(RunSpec(seed=s, scenario=scen, use_learned=True, params=dict(C.DEFAULTS), tag=f"{scen}/untuned", **base))
            # ablation: the tuned policy with the give-up guard switched off (riders cancel at 15 min)
            specs.append(RunSpec(seed=s, scenario=scen, use_learned=True, params={**C.DEFAULTS, **C.TUNED, "DEADLINE_W": 0.0},
                                 tag=f"{scen}/noguard", **base))
    day = [] if a.no_day else [RunSpec(seed=s, minutes=24 * 60, start_hour=0.0, hourly=True, tag="day/learned",
                                       fleet=a.fleet, demand=a.demand, data=a.data, city=city.name) for s in a.day_seeds]
    print(f"{len(specs)} benchmark runs + {len(day)} full-day runs on {a.workers or 'all-but-one'} cores", flush=True)
    res = run_many(day + specs, workers=a.workers)
    day_res, res = res[: len(day)], res[len(day):]

    rows: dict[tuple, dict] = {}
    for spec, r in zip(specs, res):
        row = rows.setdefault((spec.scenario, spec.seed), dict(scenario=spec.scenario, seed=spec.seed))
        kind = spec.tag.split("/")[1]
        row[{"learned": "orca_learned", "heuristic": "orca_heuristic", "untuned": "orca_untuned", "noguard": "orca_no_guard"}[kind]] = r["orca"]
        if kind == "learned":
            row["nearest"] = r["nearest"]
    rows_l = sorted(rows.values(), key=lambda r: (r["scenario"], r["seed"]))
    pols = ["nearest", "orca_heuristic", "orca_learned"] + (["orca_untuned"] if tuned else []) + ["orca_no_guard"]
    summary = {scen: {p: summarise(rows_l, scen, p) for p in pols} for scen in SCENARIOS}
    tests = {scen: dict(learned_vs_heuristic=paired_p(rows_l, scen, "orca_learned", "orca_heuristic"),
                        tuned_vs_untuned=paired_p(rows_l, scen, "orca_learned", "orca_untuned") if tuned else None,
                        learned_vs_nearest=paired_p(rows_l, scen, "orca_learned", "nearest"),
                        guard_lost=paired_p_lost(rows_l, scen, "orca_learned", "orca_no_guard")) for scen in SCENARIOS}
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

    out = dict(config=dict(minutes=a.minutes, seeds=a.seeds, fleet=a.fleet, demand=a.demand, tuned_weights=tuned, city=city.name,
                           city_label=city.label,
                           weights=dict(C.TUNED) if tuned else dict(C.DEFAULTS)),
               rows=rows_l, summary=summary, tests=tests, full_day=full_day, seconds=round(time.time() - t0))
    DOCS.mkdir(exist_ok=True)
    out["config"]["city"] = city.name
    (DOCS / f"benchmark_{city.name}.json").write_text(json.dumps(out, indent=2))
    if city.name == "manhattan":                      # the primary city also keeps the plain names
        (DOCS / "benchmark.json").write_text(json.dumps(out, indent=2))
    write_md(out, pols)
    plot(out, pols)
    print(f"Wrote docs/benchmark.json, .md, .png ({time.time() - t0:.0f}s)")


def industry(a, city, t0):
    """ORCA against the industry-standard baseline (batched matching on predicted pickup time) and nearest car.

    Each seed runs twice on identical requests, traffic, incidents and drivers: once with ORCA, once with the
    batched-ETA dispatcher in ORCA's place. Nearest car rides along in both runs and is identical in each.
    """
    base = dict(minutes=a.minutes, fleet=a.fleet, demand=a.demand, data=a.data, city=city.name)
    variants = {"orca": dict(policy="orca"), "batched": dict(policy="batched"),
                "norebal": dict(policy="orca", rebalance="off")}
    names = {"orca": "orca_learned", "batched": "batched_eta", "norebal": "orca_matching_only"}
    specs = [RunSpec(seed=s, scenario=scen, tag=f"{scen}/{v}", **variants[v], **base)
             for scen in SCENARIOS for s in a.seeds for v in variants]
    print(f"{len(specs)} runs on {a.workers or 'all-but-one'} cores", flush=True)
    res = run_many(specs, workers=a.workers)
    rows: dict[tuple, dict] = {}
    for spec, r in zip(specs, res):
        row = rows.setdefault((spec.scenario, spec.seed), dict(scenario=spec.scenario, seed=spec.seed, nearest=r["nearest"]))
        row[names[spec.tag.split("/")[1]]] = r["orca"]
    rows_l = sorted(rows.values(), key=lambda r: (r["scenario"], r["seed"]))
    pols = ["nearest", "batched_eta", "orca_matching_only", "orca_learned"]
    summary = {}
    for scen in SCENARIOS:
        summary[scen] = {p: summarise(rows_l, scen, p) for p in pols}
        rs = [r for r in rows_l if r["scenario"] == scen]
        for pol in ("orca_learned", "orca_matching_only"):
            for key in ("mean", "p95"):
                red = np.array([1 - r[pol][key] / r["batched_eta"][key] for r in rs])
                lo, hi = bootstrap_ci(red)
                summary[scen][pol][f"{key}_cut_vs_batched"] = dict(value=float(red.mean()), ci=[lo, hi])
    tests = {scen: dict(orca_vs_batched=paired_p(rows_l, scen, "orca_learned", "batched_eta"),
                        matching_vs_batched=paired_p(rows_l, scen, "orca_matching_only", "batched_eta"),
                        batched_vs_nearest=paired_p(rows_l, scen, "batched_eta", "nearest"),
                        orca_vs_batched_lost=paired_p_lost(rows_l, scen, "orca_learned", "batched_eta")) for scen in SCENARIOS}
    out = dict(config=dict(minutes=a.minutes, seeds=a.seeds, fleet=a.fleet, demand=a.demand, city=city.name, city_label=city.label),
               rows=rows_l, summary=summary, tests=tests, seconds=round(time.time() - t0))
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"industry_{city.name}.json").write_text(json.dumps(out, indent=2))
    c = out["config"]
    md = [f"# ORCA vs the industry baseline: {city.label.split(' (')[0]}", "",
          "Large ride-hailing platforms have publicly described replacing nearest-car dispatch with **batched matching**:",
          "collect requests for a few seconds, then solve one assignment that minimises total predicted pickup time.",
          "That is the baseline a dispatch system has to beat today, so this benchmark adds it.", "",
          "The batched-ETA dispatcher uses ORCA's own batching window, candidate search, ETA model and exact",
          "assignment solver, and nothing else. The gap between it and ORCA is therefore what ORCA's extra signals",
          "add (driver acceptance odds, GPS freshness, rider wait and give-up deadline, forecast-driven repositioning),",
          "not what batching adds. \"ORCA, matching only\" switches repositioning off, which splits that gap into",
          "better matching and better positioning.", "",
          f"{c['minutes']} simulated minutes per run, {c['fleet']} cars per dispatcher, {len(c['seeds'])} seeds; every policy",
          "sees identical requests, traffic, incidents and drivers on a seed. Means over seeds with 95% bootstrap",
          "confidence intervals; p-values from paired Wilcoxon signed-rank tests over seeds.", ""]
    for scen, title in (("steady", "Steady demand"), ("stress", "Spike, GPS outage and accident")):
        S, t = summary[scen], tests[scen]
        md += [f"## {title}", "", "| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |",
               "|---|---:|---:|---:|---:|---:|"]
        for p in pols:
            x = S[p]
            md.append(f"| {LABELS[p]} | {fmt_ci(x['mean'])} | {fmt_ci(x['p95'])} | {x['lost']['value']:.1f} | "
                      f"{x['reoffers']['value']:.0f} | {fmt_ci(x['reduction_vs_nearest'], pct=True) if 'reduction_vs_nearest' in x else '–'} |")
        o, m = S["orca_learned"], S["orca_matching_only"]
        md += ["", f"**ORCA vs batched ETA:** mean wait {fmt_ci(o['mean_cut_vs_batched'], pct=True)} shorter, "
                   f"p95 wait {fmt_ci(o['p95_cut_vs_batched'], pct=True)} shorter (p = {fmt_p(t['orca_vs_batched'])}). "
                   f"With repositioning switched off, ORCA's matching alone: mean wait {fmt_ci(m['mean_cut_vs_batched'], pct=True)} "
                   f"shorter, p95 {fmt_ci(m['p95_cut_vs_batched'], pct=True)} (p = {fmt_p(t['matching_vs_batched'])}). "
                   f"Batched ETA vs nearest car: p = {fmt_p(t['batched_vs_nearest'])}.", ""]
    md.append(f"Reproduce: `python -m orca.bench --city {city.name} --vs-industry` ({out['seconds'] // 60} min on this machine).")
    (DOCS / f"industry_{city.name}.md").write_text(chr(10).join(md) + chr(10), encoding="utf-8", newline=chr(10))
    print(f"Wrote docs/industry_{city.name}.json and .md ({time.time() - t0:.0f}s)")


def fmt_ci(d, unit="s", pct=False):
    if pct:
        lo, hi = d["ci"][0] * 100, d["ci"][1] * 100
        sep = " to " if lo < 0 or hi < 0 else "-"
        return f"{d['value'] * 100:.0f}% ({lo:.0f}{sep}{hi:.0f}%)"
    return f"{d['value']:.0f}{unit} ({d['ci'][0]:.0f}-{d['ci'][1]:.0f})"


def fmt_p(p):
    return "n/a" if p is None else ("< 0.001" if p < 0.001 else f"{p:.3f}")


def _note(c):
    from . import cityspec
    spec = cityspec.CITIES.get(c.get("city"))
    return spec.data_note if spec else "real trip records"


def write_md(out, pols):
    c = out["config"]
    md = ["# Benchmark", "",
          f"{c['minutes']} simulated minutes per run after a 5-minute warm-up, {c['fleet']} cars per dispatcher, "
          f"{len(c['seeds'])} seeds ({c['seeds'][0]}-{c['seeds'][-1]}). Both dispatchers see identical requests, traffic, "
          "incidents and drivers on every seed.",
          (f"Demand replays real weekday trips ({_note(c)}) at the real hourly rate; road speed follows the real "
           "median trip speed for each zone and hour." if c["demand"] == "tlc"
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
                   + f"; ORCA vs nearest p = {fmt_p(t['learned_vs_nearest'])}"
                   + (f"; riders lost with vs without the give-up guard p = {fmt_p(t.get('guard_lost'))}" if t.get("guard_lost") is not None else "")
                   + ".", ""]
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
    name = out["config"].get("city", "grid")
    text = "\n".join(md) + "\n"
    (DOCS / f"benchmark_{name}.md").write_text(text, encoding="utf-8", newline="\n")
    if name == "manhattan":
        (DOCS / "benchmark.md").write_text(text, encoding="utf-8", newline="\n")


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
    colors = {"nearest": "#8A9099", "orca_heuristic": "#E8B400", "orca_learned": "#1D1F23", "orca_untuned": "#C9CDD3",
              "orca_no_guard": "#6B3CF0"}
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
    name = out["config"].get("city", "grid")
    fig.savefig(DOCS / f"benchmark_{name}.png", dpi=160)
    if name == "manhattan":
        fig.savefig(DOCS / "benchmark.png", dpi=160)


if __name__ == "__main__":
    main()
