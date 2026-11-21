"""Shared rides: how many cars does pooling save, and what does it cost riders?

    python -m orca.poolbench --city manhattan            # fleet sweep + opt-in sweep, 6 seeds, all cores
    python -m orca.poolbench --seeds 1 2 --fleets 900 1200 --opt-in 0.5

Demand stays fixed at the level the main benchmark sizes for 1,200 cars while the fleet shrinks, so
every fleet size serves the same riders. On each seed one engine runs ORCA (solo rides) against
nearest-car, another ORCA with shared rides against nearest-car (nearest-car is identical in both).

Headline: the smallest fleet with which ORCA with shared rides still keeps the mean wait of ORCA
with 1,200 cars and solo rides, found by linear interpolation along the sweep, with a 95% bootstrap
interval over seeds. Also reported: service distance per rider (driving to pickups and with riders),
the share of riders who rode together, and how much longer their rides took than driving direct.

Writes docs/pooling_<city>.json, .md and .png.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from . import config as C
from .runs import RunSpec, bootstrap_ci, run_many

DOCS = Path(__file__).resolve().parents[2] / "docs"
REF_FLEET = 1200


def crossing(fleets: np.ndarray, waits: np.ndarray, target: float) -> float:
    """Smallest fleet at which the (fleet -> wait) curve is at or below target, by linear interpolation."""
    order = np.argsort(fleets)
    f, w = fleets[order], waits[order]
    if w[0] <= target:
        return float(f[0])
    for k in range(1, len(f)):
        if w[k] <= target:
            return float(f[k - 1] + (w[k - 1] - target) / (w[k - 1] - w[k]) * (f[k] - f[k - 1]))
    return float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 7)))
    ap.add_argument("--fleets", type=int, nargs="+", default=[700, 800, 900, 1000, 1100, 1200])
    ap.add_argument("--opt-in", type=float, nargs="+", default=[0.25, 0.5, 0.75, 1.0])
    ap.add_argument("--opt-in-fleet", type=int, default=1000)
    ap.add_argument("--minutes", type=int, default=45)
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    t0 = time.time()
    from . import city as G
    city = G.use(a.city).name
    base = dict(minutes=a.minutes, city=city, demand_fleet=REF_FLEET)
    sweep = [RunSpec(seed=s, fleet=fl, pool=pool, tag=f"fleet{fl}/{'shared' if pool else 'solo'}", **base)
             for fl in a.fleets for s in a.seeds for pool in (False, True)]
    optin = [RunSpec(seed=s, fleet=a.opt_in_fleet, pool=True, params={"POOL_OPT_IN": o}, tag=f"optin{o}", **base)
             for o in a.opt_in for s in a.seeds]
    print(f"{len(sweep) + len(optin)} runs on {city}", flush=True)
    res = run_many(sweep + optin, workers=a.workers)
    rs_sweep, rs_opt = res[:len(sweep)], res[len(sweep):]

    def table(rows, key, pol):
        return np.array([r[pol][key] for r in rows])

    by = {}
    for spec, r in zip(sweep, rs_sweep):
        by.setdefault((spec.fleet, spec.pool), {})[spec.seed] = r
    seeds = list(a.seeds)
    fleets = np.array(sorted(a.fleets))
    curves = {}
    for name, pool, pol in (("nearest", False, "nearest"), ("orca", False, "orca"), ("orca_shared", True, "orca")):
        curves[name] = {}
        for fl in fleets.tolist():
            rows = [by[(fl, pool)][s] for s in seeds]
            out = {}
            for k in ("mean", "p95", "lost", "svc_km_per_rider", "shared", "detour"):
                v = table(rows, k, pol)
                out[k] = dict(value=float(v.mean()), ci=list(bootstrap_ci(v)))
            curves[name][fl] = out
    # per-seed matrices (seed x fleet) of mean wait, for the crossing and its bootstrap
    W = {name: np.array([[by[(fl, pool)][s][pol]["mean"] for fl in fleets.tolist()] for s in seeds])
         for name, pool, pol in (("nearest", False, "nearest"), ("orca", False, "orca"), ("orca_shared", True, "orca"))}
    ref = W["orca"][:, fleets.tolist().index(REF_FLEET)] if REF_FLEET in fleets else None
    headline = None
    if ref is not None:
        def cross(idx):
            return crossing(fleets.astype(float), W["orca_shared"][idx].mean(axis=0), ref[idx].mean())
        point = cross(np.arange(len(seeds)))
        rng = np.random.default_rng(0)
        boots = np.array([cross(rng.integers(0, len(seeds), len(seeds))) for _ in range(2000)])
        boots = boots[np.isfinite(boots)]
        lo, hi = (np.quantile(boots, [0.025, 0.975]).tolist() if len(boots) else [float("nan")] * 2)
        headline = dict(ref_wait=float(ref.mean()), fleet_needed=point, fleet_ci=[lo, hi],
                        cars_saved=1 - point / REF_FLEET if np.isfinite(point) else None,
                        cars_saved_ci=[1 - hi / REF_FLEET, 1 - lo / REF_FLEET] if np.isfinite(point) else None,
                        nearest_at_ref=float(W["nearest"][:, fleets.tolist().index(REF_FLEET)].mean()))
    opt = {}
    for spec, r in zip(optin, rs_opt):
        opt.setdefault(spec.params["POOL_OPT_IN"], []).append(r["orca"])
    opt_summary = {str(o): {k: float(np.mean([x[k] for x in rows])) for k in ("mean", "shared", "detour", "svc_km_per_rider")}
                   for o, rows in sorted(opt.items())}
    out = dict(config=dict(city=city, seeds=seeds, fleets=fleets.tolist(), minutes=a.minutes, demand_for=REF_FLEET,
                           pool={k: v for k, v in C.POOL_DEFAULTS.items()}, opt_in_fleet=a.opt_in_fleet),
               curves={n: {str(k): v for k, v in c.items()} for n, c in curves.items()}, headline=headline,
               opt_in=opt_summary, seconds=round(time.time() - t0))
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"pooling_{city}.json").write_text(json.dumps(out, indent=2))
    write_md(out)
    plot(out)
    print(f"Wrote docs/pooling_{city}.json, .md, .png ({time.time() - t0:.0f}s)")


def write_md(out):
    c, h, cv = out["config"], out["headline"], out["curves"]
    p = c["pool"]
    md = ["# Shared rides", "",
          f"Real {c['city'].capitalize()} demand sized for {c['demand_for']:,} cars, served by smaller and smaller fleets; "
          f"{c['minutes']} simulated minutes per run, seeds {c['seeds'][0]}-{c['seeds'][-1]}, paired. "
          f"{p['POOL_OPT_IN']:.0%} of riders accept a shared ride (the same riders for every policy); a car carries up to "
          f"{p['POOL_CAP']} riders; every pickup stays inside the rider's patience and every shared ride within "
          f"{1 + p['POOL_DETOUR']:.1f}x the direct drive plus {p['POOL_SLACK']:.0f} s.", ""]
    if h and h["cars_saved"] is not None:
        md += [f"**With shared rides ORCA keeps the {h['ref_wait']:.0f} s mean wait of {c['demand_for']:,} solo cars with about "
               f"{h['fleet_needed']:.0f} cars: {h['cars_saved'] * 100:.0f}% fewer "
               f"(95% interval {h['cars_saved_ci'][0] * 100:.0f}-{h['cars_saved_ci'][1] * 100:.0f}%).** "
               f"Nearest-car dispatch with all {c['demand_for']:,} cars averages {h['nearest_at_ref']:.0f} s.", ""]
    md += ["| Cars | Nearest car | ORCA, solo rides | ORCA, shared rides | Riders sharing | Shared ride vs direct | Service km per rider, solo / shared |",
           "|---:|---:|---:|---:|---:|---:|---:|"]
    for fl in c["fleets"]:
        n, o, s = cv["nearest"][str(fl)], cv["orca"][str(fl)], cv["orca_shared"][str(fl)]
        md.append(f"| {fl:,} | {n['mean']['value']:.0f}s | {o['mean']['value']:.0f}s | {s['mean']['value']:.0f}s "
                  f"({s['mean']['ci'][0]:.0f}-{s['mean']['ci'][1]:.0f}) | {s['shared']['value'] * 100:.0f}% | "
                  f"{s['detour']['value']:.2f}x | {o['svc_km_per_rider']['value']:.2f} / {s['svc_km_per_rider']['value']:.2f} |")
    md += ["", "Mean wait; 95% bootstrap interval over seeds for ORCA with shared rides in brackets. \"Shared ride vs direct\" is "
               "time on board over the direct drive, for riders who rode with someone. Service km count driving to pickups and "
               "with riders on board.", "",
           f"## How many riders opt in ({c['opt_in_fleet']:,} cars)", "",
           "| Riders accepting a shared ride | Mean wait | Riders sharing | Shared ride vs direct |", "|---:|---:|---:|---:|"]
    for o, v in out["opt_in"].items():
        md.append(f"| {float(o) * 100:.0f}% | {v['mean']:.0f}s | {v['shared'] * 100:.0f}% | {v['detour']:.2f}x |")
    (DOCS / f"pooling_{c['city']}.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")


def plot(out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print("chart skipped:", exc)
        return
    c, cv, h = out["config"], out["curves"], out["headline"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw=dict(width_ratios=[1.4, 1]))
    ax = axes[0]
    fl = c["fleets"]
    for name, col, lab in (("nearest", "#8A9099", "Nearest car"), ("orca", "#1D1F23", "ORCA, solo rides"),
                           ("orca_shared", "#E8B400", "ORCA, shared rides")):
        v = [cv[name][str(f)]["mean"]["value"] for f in fl]
        lo = [cv[name][str(f)]["mean"]["ci"][0] for f in fl]
        hi = [cv[name][str(f)]["mean"]["ci"][1] for f in fl]
        ax.plot(fl, v, color=col, lw=2.2, marker="o", ms=4, label=lab)
        ax.fill_between(fl, lo, hi, color=col, alpha=0.15, lw=0)
    if h:
        ax.axhline(h["ref_wait"], color="#1D1F23", lw=1, ls="--")
        if h["cars_saved"] is not None:
            ax.axvline(h["fleet_needed"], color="#E8B400", lw=1, ls="--")
    ax.set_xlabel(f"cars (demand fixed at the level for {c['demand_for']:,})")
    ax.set_ylabel("mean wait, s")
    ax.set_title("Mean wait by fleet size, 95% CI", loc="left", fontsize=11)
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax = axes[1]
    oi = sorted(out["opt_in"], key=float)
    x = [float(o) * 100 for o in oi]
    ax.plot(x, [out["opt_in"][o]["mean"] for o in oi], color="#E8B400", lw=2.2, marker="o", ms=4)
    ax.set_xlabel("riders accepting a shared ride, %")
    ax.set_ylabel("mean wait, s")
    ax2 = ax.twinx()
    ax2.bar(x, [out["opt_in"][o]["shared"] * 100 for o in oi], width=8, color="#1D1F23", alpha=0.18)
    ax2.set_ylabel("riders who shared, %")
    ax.set_title(f"Opt-in, {c['opt_in_fleet']:,} cars", loc="left", fontsize=11)
    ax.spines[["top"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(DOCS / f"pooling_{c['city']}.png", dpi=160)


if __name__ == "__main__":
    main()
