"""What traffic-aware routing changes: routes by hour and around accidents, on the real street networks.

    python -m orca.routing_report                  # every built city; writes docs/routing.md and docs/routing.json

For random origin-destination pairs between pickup points, each route is timed under the true
conditions the simulator drives in (the real speed of every zone at that hour, and the slowdown
around an accident), and compared with the route a driver would take if the router ignored them:

* By hour: routes chosen on free-flow weights vs on the hour's real zone speeds, at 08:00, 17:00 and 03:00.
* Accidents: a crash (speed down 78% within about 600 m) at a random point; for routes that pass
  through it, the route chosen without knowing vs the one around it.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

from . import city as G
from . import cityspec

DOCS = Path(__file__).resolve().parents[2] / "docs"


def route_time(c, u: int, v: int, nxt: np.ndarray, w: np.ndarray, near: np.ndarray | None = None) -> tuple[float, bool]:
    tot, x, steps, hit = 0.0, u, 0, False
    while x != v and steps < c.n:
        y = int(nxt[x])
        if y < 0:
            return float("inf"), hit
        e = c.edge_of[(x, y)]
        tot += w[e]
        hit |= near is not None and bool(near[e])
        x, steps = y, steps + 1
    return tot, hit


def by_hour(c, hours=(8, 17, 3), pairs=400, seed=0) -> dict:
    rng = random.Random(seed)
    od = [(c.nearest_node(*c.random_point(rng)), c.nearest_node(*c.random_point(rng))) for _ in range(pairs)]
    c.set_conditions(-1.0, [])
    free = {v: c.tree_to(v)[1] for _, v in od}
    out = {}
    for h in hours:
        true_w = c._w_free / c.zone_hour[c.edge_zone, h]
        c.set_conditions(h * 3600 + 60.0, [])
        aware = {v: c.tree_to(v)[1] for _, v in od}
        save, changed = [], 0
        for u, v in od:
            a, _ = route_time(c, u, v, free[v], true_w)
            b, _ = route_time(c, u, v, aware[v], true_w)
            if a > 0 and np.isfinite(a):
                save.append((a - b) / a)
                changed += (a - b) > 1e-9
        s = np.array(save)
        out[f"{h:02d}:00"] = dict(pairs=len(s), changed=changed / len(s), mean=float(s.mean()), p90=float(np.percentile(s, 90)),
                                  max=float(s.max()))
    c.set_conditions(-1.0, [])
    return out


def accidents(c, trials=30, pairs=150, seed=1, hour=17) -> dict:
    rng = random.Random(seed)
    save = []
    for _ in range(trials):
        ax, az = c.random_point(rng)
        r, s = 20.0, 0.78                                   # as World.trigger_accident
        d2 = (c.edge_mx - ax) ** 2 + (c.edge_mz - az) ** 2
        near = d2 < r * r
        true_w = c._w_free / c.zone_hour[c.edge_zone, hour] / np.maximum(0.22, 1 - s * np.exp(-d2 / (2 * r * r)))
        od = [(c.nearest_node(*c.random_point(rng)), c.nearest_node(*c.random_point(rng))) for _ in range(pairs)]
        c.set_conditions(hour * 3600 + 60.0, [])
        blind = {v: c.tree_to(v)[1] for _, v in od}
        c.set_conditions(hour * 3600 + 60.0, [(ax, az, r, s)])
        aware = {v: c.tree_to(v)[1] for _, v in od}
        for u, v in od:
            a, through = route_time(c, u, v, blind[v], true_w, near)
            if through and a > 0:
                b, _ = route_time(c, u, v, aware[v], true_w)
                save.append((a - b) / a)
    c.set_conditions(-1.0, [])
    s = np.array(save)
    return dict(routes=len(s), mean=float(s.mean()), median=float(np.median(s)), p90=float(np.percentile(s, 90)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cities", nargs="+", default=None)
    a = ap.parse_args()
    out = {}
    for name in a.cities or cityspec.built():
        c = G.use(name)
        if c.zone_hour is None:
            continue
        out[name] = dict(label=c.city_label, by_hour=by_hour(c), accidents=accidents(c))
        print(name, json.dumps(out[name]), flush=True)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "routing.json").write_text(json.dumps(out, indent=2))
    md = ["# Traffic-aware routing", "",
          "Drivers route by the real speed of every zone at the current hour (measured from the city's own trip records) "
          "and around live accidents, like a navigation app. Each route below is timed under the conditions the simulator "
          "actually drives in, and compared with the route chosen without that knowledge. Produced by "
          "`python -m orca.routing_report`.", "",
          "| City | Hour | Routes that change | Driving time saved: mean | 90th percentile | Most |", "|---|---|---:|---:|---:|---:|"]
    for r in out.values():
        for h, v in r["by_hour"].items():
            md.append(f"| {r['label']} | {h} | {v['changed'] * 100:.0f}% | {v['mean'] * 100:.1f}% | {v['p90'] * 100:.1f}% | {v['max'] * 100:.0f}% |")
    md += ["", "Speeds are known per taxi zone (neighbourhood in Chicago), not per street, so most routes keep their shape and "
               "the average gain is small; it is largest across zones that slow down unevenly at rush hour.", "",
           "## Around an accident (17:00)", "", "| City | Routes through the accident | Time saved: mean | median | 90th percentile |",
           "|---|---:|---:|---:|---:|"]
    for r in out.values():
        v = r["accidents"]
        md.append(f"| {r['label']} | {v['routes']} | {v['mean'] * 100:.1f}% | {v['median'] * 100:.1f}% | {v['p90'] * 100:.1f}% |")
    (DOCS / "routing.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print("Wrote docs/routing.md")


if __name__ == "__main__":
    main()
