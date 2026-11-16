"""Tune ORCA's scoring weights by successive halving over head-to-head simulations.

    python -m orca.tune                      # ~10 min on 8 cores; writes artifacts/dispatch_params.json
    python -m orca.tune --candidates 48 --minutes 30

Objective: mean rider wait averaged over a steady run and a stress run (spike, GPS outage,
accident), plus 900 s for every rider who gave up. Tuning uses seeds 101+, which the
benchmark (seeds 1-10) never sees, so the reported benchmark is not fitted to its own seeds.

Rounds: every candidate is evaluated on a few short runs; the best third survives and is
re-evaluated on more seeds; the winner must also beat the hand-set defaults on the final
round's seeds, otherwise the defaults are kept.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from . import config as C
from .models import ARTIFACTS
from .runs import RunSpec, run_many

# Search space: (low, high, log-scale)
SPACE = {
    "REOFFER": (15.0, 120.0, True),
    "COVER": (0.2, 1.2, False),
    "ZONE_SLOPE": (5.0, 60.0, True),
    "ZONE_CAP": (20.0, 180.0, True),
    "URGENT_W": (0.3, 1.0, False),
    "WAIT_W": (0.0, 0.6, False),
    "DEADLINE_W": (0.0, 3.0, False),
    "K": (6, 16, False),
}
LOST_PENALTY = 900.0


def sample(rng: np.random.Generator) -> dict:
    out = {}
    for k, (lo, hi, log) in SPACE.items():
        v = float(np.exp(rng.uniform(np.log(lo), np.log(hi)))) if log else float(rng.uniform(lo, hi))
        out[k] = round(v) if k == "K" else round(v, 3)
    return out


def evaluate(cands: list[dict], seeds: list[int], minutes: int, label: str) -> np.ndarray:
    from . import city as G
    name = G.use().name                                # workers run this city, explicitly
    specs = [RunSpec(seed=s, minutes=minutes, scenario=sc, params=p, tag=f"{label} c{i}", warm=240, city=name,
                     events=tuple((round(t * minutes / 45), ev) for t, ev in ((20, "spike"), (20, "outage"), (40, "accident")))
                     if sc == "stress" else ())
             for i, p in enumerate(cands) for s in seeds for sc in ("steady", "stress")]
    res = run_many(specs, progress=False)
    score = np.zeros(len(cands))
    for spec, r in zip(specs, res):
        i = int(spec.tag.split(" c")[1])
        score[i] += r["orca"]["mean"] + LOST_PENALTY * r["orca"]["lost"] / max(1, r["orca"]["served"] + r["orca"]["lost"])
    return score / (2 * len(seeds))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", type=int, default=36)
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    rng = np.random.default_rng(a.seed)
    defaults = dict(C.DEFAULTS)
    cands = [defaults] + [sample(rng) for _ in range(a.candidates - 1)]
    rounds = [([101, 102], a.minutes), ([103, 104, 105], a.minutes), ([106, 107, 108, 109], round(a.minutes * 1.5))]
    history = []
    for r, (seeds, minutes) in enumerate(rounds, 1):
        print(f"Round {r}: {len(cands)} candidates x {len(seeds)} seeds x 2 scenarios, {minutes} min each", flush=True)
        score = evaluate(cands, seeds, minutes, f"r{r}")
        order = np.argsort(score)
        history.append([dict(params=cands[i], score=float(score[i])) for i in order])
        for i in order[:5]:
            tag = " (hand-set defaults)" if cands[i] == defaults else ""
            print(f"  {score[i]:7.2f}  {cands[i]}{tag}", flush=True)
        if r < len(rounds):
            keep = [cands[i] for i in order[: max(3, len(cands) // 3)]]
            cands = keep if defaults in keep else [*keep, defaults]     # always carry the defaults as a control
    final = history[-1]
    best = final[0]
    base = next(h for h in final if h["params"] == defaults)
    gain = 1 - best["score"] / base["score"]
    chosen = best["params"] if best["params"] != defaults and gain > 0.01 else defaults
    out = dict(params=chosen, objective="mean wait over steady + stress runs, +900 s per rider lost",
               tuning_seeds=sorted({s for seeds, _ in rounds for s in seeds}), defaults=defaults,
               final_round=dict(best=best, defaults=base, improvement=gain), rounds=history,
               tuned_at=time.strftime("%Y-%m-%d %H:%M"), seconds=round(time.time() - t0))
    ARTIFACTS.mkdir(exist_ok=True)
    from . import city as G
    from .models import artifact
    G.use()
    out["city"] = G.current().name
    artifact("dispatch_params", "json").write_text(json.dumps(out, indent=2))
    print(f"\nFinal round: best {best['score']:.2f} vs defaults {base['score']:.2f} ({gain * 100:+.1f}%).")
    print(f"Saved {artifact('dispatch_params', 'json').name} with {'tuned' if chosen != defaults else 'default'} weights "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
