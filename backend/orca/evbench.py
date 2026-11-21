"""Electric fleet: a 12-hour shift on real demand, real charging stations, and who charges how.

    python -m orca.evbench --city manhattan          # 6 seeds, all cores
    python -m orca.evbench --seeds 1 2 --hours 6

Every car is a 75 kWh EV starting the shift between 35% and 100% charged (the same batteries for
every policy); stations and plug counts are the city's public chargers from OpenStreetMap
(orca.chargers). Policies, paired on each seed:

  nearest           nearest-car dispatch; drivers charge on their own (nearest fast charger at 20%)
  orca_naive        ORCA dispatch; drivers still charge on their own
  orca_smart        ORCA dispatch and charging: the station that gets the car back soonest
  orca_topup        ORCA smart charging, plus early top-ups while the city has cars to spare

Writes docs/ev_<city>.json and .md.
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
LABELS = {"nearest": "Nearest car, drivers charge on their own", "orca_naive": "ORCA, drivers charge on their own",
          "orca_smart": "ORCA, ORCA plans charging", "orca_topup": "ORCA plans charging, with early top-ups"}
KEYS = ("mean", "p95", "lost", "served", "queue_wait", "charge_hours", "sessions", "soc_mean", "stranded")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=None)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 7)))
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--start", type=float, default=7.0)
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    t0 = time.time()
    from . import chargers
    from . import city as G
    city = G.use(a.city)
    st = chargers.load(city)
    base = dict(minutes=int(a.hours * 60), city=city.name, start_hour=a.start, fleet=a.fleet)
    variants = {"orca_naive": ("naive", None), "orca_smart": ("smart", None), "orca_topup": ("smart", {"EV_TOPUP": 0.4})}
    specs = [RunSpec(seed=s, ev=ev, params=p, tag=k, **base) for k, (ev, p) in variants.items() for s in a.seeds]
    print(f"{len(specs)} runs of {a.hours:g} h on {city.name}", flush=True)
    res = run_many(specs, workers=a.workers)
    rows = {}
    for spec, r in zip(specs, res):
        rows.setdefault(spec.tag, {})[spec.seed] = r["orca"]
        if spec.tag == "orca_smart":
            rows.setdefault("nearest", {})[spec.seed] = r["nearest"]
    summary = {}
    for pol in ("nearest", *variants):
        per = [rows[pol][s] for s in a.seeds]
        summary[pol] = {k: dict(value=float(np.mean([p[k] for p in per])), ci=list(bootstrap_ci(np.array([p[k] for p in per]))))
                        for k in KEYS}
    out = dict(config=dict(city=city.name, label=city.city_label, seeds=a.seeds, hours=a.hours, start=a.start, fleet=a.fleet,
                           battery_kwh=C.EV_BATTERY_KWH, kwh_per_km=C.EV_KWH_PER_KM, soc0=list(C.EV_SOC0), low=C.EV_LOW,
                           target=C.EV_TARGET,
                           stations=len(st["x"]) if st else 0, fast_stations=int(st["dc"].sum()) if st else 0,
                           fast_plugs=int(st["plugs"][st["dc"]].sum()) if st else 0, plugs=int(st["plugs"].sum()) if st else 0),
               summary=summary, seconds=round(time.time() - t0))
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"ev_{city.name}.json").write_text(json.dumps(out, indent=2))
    write_md(out)
    print(f"Wrote docs/ev_{city.name}.json and .md ({time.time() - t0:.0f}s)")


def write_md(out):
    c, S = out["config"], out["summary"]
    md = ["# Electric fleet", "",
          f"{c['label']}: {c['fleet']:,} electric cars per dispatcher over a {c['hours']:g}-hour shift from {c['start']:02.0f}:00 on "
          f"real demand, seeds {c['seeds'][0]}-{c['seeds'][-1]}, paired. Each car has a {c['battery_kwh']:.0f} kWh battery using "
          f"{c['kwh_per_km']} kWh per km and starts between {c['soc0'][0]:.0%} and {c['soc0'][1]:.0%} charged; below "
          f"{c['low']:.0%} it stops taking rides and charges to {c['target']:.0%}; a ride is offered only to a car that can "
          f"finish it with charge to spare. Charging stations are the city's {c['stations']} public stations in OpenStreetMap "
          f"({c['fast_stations']} fast, {c['fast_plugs']} fast plugs; {c['plugs']} plugs in all).", "",
          "| Policy | Mean wait | p95 wait | Riders lost | Queue at the charger | Car-hours charging | Sessions | Charge at the end |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for pol, lab in LABELS.items():
        s = S[pol]
        md.append(f"| {lab} | {s['mean']['value']:.0f}s ({s['mean']['ci'][0]:.0f}-{s['mean']['ci'][1]:.0f}) | {s['p95']['value']:.0f}s | "
                  f"{s['lost']['value']:.0f} | {s['queue_wait']['value'] / 60:.1f} min | {s['charge_hours']['value']:.0f} | "
                  f"{s['sessions']['value']:.0f} | {s['soc_mean']['value']:.0%} |")
    o, sm = S["orca_naive"], S["orca_smart"]
    md += ["", "Means over seeds, 95% bootstrap interval for the mean wait in brackets. \"Car-hours charging\" counts driving to a "
               "station, queueing and charging. No car ran out of charge in any run." if all(S[p]["stranded"]["value"] == 0 for p in S)
           else "", "",
           f"Left to themselves, drivers all head for the nearest fast charger and queue {o['queue_wait']['value'] / 60:.1f} minutes "
           f"on average. ORCA sends each car to the station that returns it to service soonest, counting the cars already "
           f"plugged in, queued and on their way, and the charging time at that station's power: "
           + (f"the queue drops to {sm['queue_wait']['value'] / 60:.1f} minutes" if sm["queue_wait"]["value"] < o["queue_wait"]["value"]
              else f"with fast plugs plentiful it accepts a longer queue ({sm['queue_wait']['value'] / 60:.1f} minutes) at faster or "
                   f"closer stations")
           + f" and the fleet spends {(1 - sm['charge_hours']['value'] / o['charge_hours']['value']) * 100:.0f}% fewer car-hours charging. "
           f"Early top-ups keep batteries fuller but take cars off the road; on this shift they do not shorten waits, so they "
           f"are off by default.", ""]
    (DOCS / f"ev_{c['city']}.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
