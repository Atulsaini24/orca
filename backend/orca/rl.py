"""Learned repositioning: a value network for idle cars, trained by fitted TD learning with policy iteration.

    python -m orca.rl train --city manhattan        # writes artifacts/reposition_value_<city>.npz and _metrics.json
    python -m orca.rl bench --city manhattan        # rules vs learned vs none on the benchmark seeds: docs/rl_<city>.md

The idea follows the value-based dispatching used at scale in ride hailing (Xu et al., KDD 2018; Tang
et al., KDD 2019): learn V(s), the discounted number of rides a car can expect from becoming idle in
state s, where s is its zone, the time of day and the live supply and forecast demand around it.

* Data: every time a car becomes idle, its state is logged; the transition to the next time it becomes
  idle (after one ride) earns reward 1 and is discounted by the time that took, gamma per minute.
* Learning: fitted TD(0) with a target network, Huber loss and Adam, on a small MLP with a learned zone
  embedding (NumPy only; inference runs inside the engine without any ML framework).
* Policy: every rebalancing round, idle cars move one at a time to the neighbouring zone with the best
  advantage V(there) - V(here) - a small cost per minute of driving, above a margin (a repositioning car
  stays available for rides, so the drive is not lost time), with the supply features updated after each
  move and the same budget as the hand-written rules (1.5% of the fleet per round).
* Policy iteration: collect with the current policy (the hand-written rules first, then the learned
  value with some exploration), refit on everything collected, repeat. Training seeds are 201+; the
  benchmark uses seeds 1-10, never seen in training.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from . import city as G
from . import config as C

GAMMA = 0.96                      # discount per minute
MARGIN = 0.02                     # advantage (in rides) a move must clear
MOVE_COST = 0.004                 # rides per minute of repositioning (mileage; a moving car can still be dispatched)
N_FEAT = 9
EMB = 8
HIDDEN = 64
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
DOCS = Path(__file__).resolve().parents[2] / "docs"


def net_path(city: str) -> Path:
    return ARTIFACTS / f"reposition_value_{city}.npz"


# ---- features --------------------------------------------------------------------------------------
class ZoneContext:
    """Static zone geometry for one city: neighbour weights and centre-to-centre free-flow drive."""

    def __init__(self, city):
        n = city.N_ZONES
        self.n = n
        cx = np.array([city.zone_center(i)[0] for i in range(n)])
        cz = np.array([city.zone_center(i)[1] for i in range(n)])
        self.cx, self.cz = cx, cz
        dist = np.array([[city.zone_dist(a, b) for b in range(n)] for a in range(n)])
        self.nb = ((dist > 0) & (dist < 1.6)).astype(float)
        self.near = [np.array([j for j in np.argsort(dist[i]) if j != i and dist[i, j] < 2.9][:6], dtype=np.int64)
                     for i in range(n)]
        self.ff = city.ff_matrix(cx, cz, cx, cz)          # free-flow drive between zone centres, at unit speed


def zone_features(ctx: ZoneContext, z_idle: np.ndarray, need: np.ndarray, clock: float, fleet: int) -> np.ndarray:
    """One row per zone: time of day, supply and forecast demand here and next door, citywide balance."""
    h = (clock / 3600) % 24 / 24 * math.tau
    idle_nb, need_nb = ctx.nb @ z_idle, ctx.nb @ need
    F = np.empty((ctx.n, N_FEAT))
    F[:, 0], F[:, 1] = math.sin(h), math.cos(h)
    F[:, 2], F[:, 3] = np.log1p(z_idle), np.log1p(need)
    F[:, 4] = np.log((need + 1) / (z_idle + 1))
    F[:, 5], F[:, 6] = np.log1p(idle_nb), np.log1p(need_nb)
    F[:, 7] = z_idle.sum() / max(1, fleet) * 4
    F[:, 8] = np.log1p(need.sum())
    return F


# ---- the value network (NumPy) ---------------------------------------------------------------------
class ValueNet:
    def __init__(self, n_zones: int, seed: int = 0, params: dict | None = None):
        if params is not None:
            self.p = {k: np.asarray(v, dtype=float) for k, v in params.items()}
            return
        r = np.random.default_rng(seed)
        d_in = EMB + N_FEAT
        self.p = dict(E=r.normal(0, 0.1, (n_zones, EMB)), W1=r.normal(0, math.sqrt(2 / d_in), (d_in, HIDDEN)),
                      b1=np.zeros(HIDDEN), W2=r.normal(0, math.sqrt(2 / HIDDEN), (HIDDEN, HIDDEN)), b2=np.zeros(HIDDEN),
                      w3=r.normal(0, 0.05, HIDDEN), b3=np.array([5.0]))

    def __call__(self, z: np.ndarray, X: np.ndarray) -> np.ndarray:
        p = self.p
        h1 = np.maximum(0, np.c_[p["E"][z], X] @ p["W1"] + p["b1"])
        h2 = np.maximum(0, h1 @ p["W2"] + p["b2"])
        return h2 @ p["w3"] + p["b3"][0]

    def grads(self, z, X, y):
        """Huber-loss gradients by backpropagation."""
        p = self.p
        a0 = np.c_[p["E"][z], X]
        h1 = np.maximum(0, a0 @ p["W1"] + p["b1"])
        h2 = np.maximum(0, h1 @ p["W2"] + p["b2"])
        out = h2 @ p["w3"] + p["b3"][0]
        err = out - y
        loss = float(np.mean(np.where(np.abs(err) < 1, 0.5 * err ** 2, np.abs(err) - 0.5)))
        g = np.clip(err, -1, 1) / len(y)
        gr = dict(w3=h2.T @ g, b3=np.array([g.sum()]))
        d2 = np.outer(g, p["w3"]) * (h2 > 0)
        gr["W2"], gr["b2"] = h1.T @ d2, d2.sum(0)
        d1 = d2 @ p["W2"].T * (h1 > 0)
        gr["W1"], gr["b1"] = a0.T @ d1, d1.sum(0)
        dE = d1 @ p["W1"][:EMB].T
        gE = np.zeros_like(p["E"])
        np.add.at(gE, z, dE)
        gr["E"] = gE
        return loss, gr

    def save(self, path: Path, meta: dict):
        np.savez(path, **self.p, meta=json.dumps(meta))

    @classmethod
    def load(cls, path: Path) -> ValueNet | None:
        if not path.exists():
            return None
        with np.load(path, allow_pickle=False) as d:
            return cls(len(d["E"]), params={k: d[k] for k in d.files if k != "meta"})


def fit_td(net: ValueNet, data: dict, rounds: int = 8, epochs: int = 6, lr: float = 2e-3, seed: int = 0) -> list[float]:
    """Fitted TD(0): regress V(s) on 1 + gamma^(dt/60) V_target(s') for a few rounds, refreshing the target."""
    rng = np.random.default_rng(seed)
    z0, X0, z1, X1, disc, alive = data["z0"], data["X0"], data["z1"], data["X1"], GAMMA ** (data["dt"] / 60), data["alive"]
    m = {k: np.zeros_like(v) for k, v in net.p.items()}
    v = {k: np.zeros_like(x) for k, x in net.p.items()}
    step, losses = 0, []
    for _ in range(rounds):
        target = ValueNet(len(net.p["E"]), params={k: x.copy() for k, x in net.p.items()})
        y = 1.0 + disc * np.where(alive, target(z1, X1), 0.0)
        for _ in range(epochs):
            order = rng.permutation(len(y))
            for s in range(0, len(y), 256):
                b = order[s:s + 256]
                loss, gr = net.grads(z0[b], X0[b], y[b])
                step += 1
                for k in net.p:                                # Adam
                    m[k] = 0.9 * m[k] + 0.1 * gr[k]
                    v[k] = 0.999 * v[k] + 0.001 * gr[k] ** 2
                    net.p[k] -= lr * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v[k] / (1 - 0.999 ** step)) + 1e-8)
            losses.append(loss)
    return losses


# ---- logging transitions inside the engine ---------------------------------------------------------
class Recorder:
    """Logs each car's idle states; consecutive ones form a transition with one ride in between."""

    def __init__(self, d):
        self.d = d
        self.ctx = ZoneContext(d.city)
        self.last: dict[int, tuple] = {}
        self.rows: list[tuple] = []

    def on_idle(self, i: int):
        d = self.d
        f = d.f
        z = int(d.city.zone_of(f.x[i], f.z[i]))
        F = zone_features(self.ctx, d.z_idle.astype(float), np.asarray(d.forecaster.forecast, dtype=float) * C.COVER,
                          d.world.clock(), d.n)[z]
        t = d.world.t
        prev = self.last.get(i)
        if prev is not None:
            self.rows.append((prev[0], prev[1], z, F, t - prev[2]))
        self.last[i] = (z, F, t)

    def arrays(self) -> dict:
        if not self.rows:
            return dict(z0=np.zeros(0, int), X0=np.zeros((0, N_FEAT)), z1=np.zeros(0, int), X1=np.zeros((0, N_FEAT)),
                        dt=np.zeros(0), alive=np.zeros(0, bool))
        z0, X0, z1, X1, dt = zip(*self.rows)
        return dict(z0=np.array(z0), X0=np.array(X0), z1=np.array(z1), X1=np.array(X1), dt=np.array(dt),
                    alive=np.ones(len(dt), bool))


# ---- the learned policy ----------------------------------------------------------------------------
class LearnedRebalancer:
    def __init__(self, d, net: ValueNet, explore: float = 0.0, seed: int = 0):
        self.d, self.net, self.explore = d, net, explore
        self.ctx = ZoneContext(d.city)
        self.rng = np.random.default_rng(seed)
        self.moves = 0

    def step(self, free: np.ndarray, zones: np.ndarray, need: np.ndarray) -> int:
        """Move idle cars (free, in zones) by learned advantage; returns how many moved."""
        d, ctx = self.d, self.ctx
        by_zone = [list(free[zones == z]) for z in range(ctx.n)]
        z_idle = d.z_idle.astype(float).copy()
        drive_min = ctx.ff / d.world.speed_now() / 60
        budget, moved = math.ceil(d.n * 0.015), 0
        allz = np.arange(ctx.n)
        while budget > 0:
            V = self.net(allz, zone_features(ctx, z_idle, need, d.world.clock(), d.n))
            best, bi, bj = MARGIN, -1, -1
            for i in range(ctx.n):
                if not by_zone[i]:
                    continue
                nb = ctx.near[i]
                if not len(nb):
                    continue
                if self.explore and self.rng.random() < self.explore:
                    j = int(nb[self.rng.integers(len(nb))])
                    adv = MARGIN + 1e-6
                else:
                    a = V[nb] - V[i] - MOVE_COST * drive_min[i, nb]
                    k = int(np.argmax(a))
                    j, adv = int(nb[k]), float(a[k])
                if adv > best:
                    best, bi, bj = adv, i, j
            if bi < 0:
                break
            car = by_zone[bi].pop()
            d.route_to(car, *d.city.point_in_zone(bj, d.rng))
            d.f.rebal[car], d.f.cruise_wait[car] = True, 0.0
            z_idle[bi] -= 1
            z_idle[bj] += 1
            budget -= 1
            moved += 1
        self.moves += moved
        return moved


# ---- training and evaluation -----------------------------------------------------------------------
def collect(city: str, iteration: int, net_file: str | None, seeds, hours, explore: float, workers=None) -> dict:
    from .runs import RunSpec, run_many
    specs = [RunSpec(seed=201 + 50 * iteration + s, minutes=60, city=city, start_hour=float(h), rl_log=True,
                     rebalance="learned" if net_file else "rules", rl_net=net_file, rl_explore=explore,
                     tag=f"rl-collect{iteration}") for s in seeds for h in hours]
    res = run_many(specs, workers=workers, progress=False)
    parts = [r["rl"] for r in res]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def train(city: str, iters: int = 4, workers=None):
    t0 = time.time()
    G.use(city)
    n_zones = G.current().N_ZONES
    hours = [6, 8, 10, 13, 16, 18, 21, 23]
    tmp = ARTIFACTS / f"reposition_value_{city}.tmp.npz"
    net, data, log = ValueNet(n_zones), None, []
    for it in range(iters):
        new = collect(city, it, str(tmp) if it else None, seeds=(0, 1), hours=hours, explore=0.1 if it else 0.0, workers=workers)
        data = new if data is None else {k: np.concatenate([data[k], new[k]]) for k in data}
        losses = fit_td(net, data, seed=it)
        net.save(tmp, {})
        v = net(data["z0"], data["X0"])
        log.append(dict(iteration=it, transitions=len(new["dt"]), total=len(data["dt"]), loss=losses[-1],
                        v_mean=float(v.mean()), v_std=float(v.std())))
        print(f"iteration {it}: {len(new['dt']):,} new transitions ({len(data['dt']):,} in all), "
              f"TD loss {losses[-1]:.4f}, V mean {v.mean():.2f} sd {v.std():.2f}", flush=True)
    meta = dict(city=city, gamma_per_min=GAMMA, margin=MARGIN, iterations=log, trained_at=time.strftime("%Y-%m-%d %H:%M"),
                seconds=round(time.time() - t0))
    net.save(net_path(city), meta)
    (ARTIFACTS / f"reposition_value_metrics_{city}.json").write_text(json.dumps(meta, indent=2))
    tmp.unlink(missing_ok=True)
    print(f"Wrote {net_path(city).name} ({time.time() - t0:.0f}s)")


def bench(city: str, seeds, workers=None):
    from .runs import RunSpec, bootstrap_ci, run_many
    t0 = time.time()
    G.use(city)
    modes = ("rules", "learned", "off")
    scen = [("morning", 7.5), ("evening", 17.0), ("night", 22.0)]
    specs = [RunSpec(seed=s, minutes=45, city=city, start_hour=h, rebalance=m, tag=f"{name}/{m}")
             for name, h in scen for m in modes for s in seeds]
    res = run_many(specs, workers=workers, progress=False)
    out = {}
    for name, _ in scen:
        for m in modes:
            v = np.array([r["orca"]["mean"] for sp, r in zip(specs, res) if sp.tag == f"{name}/{m}"])
            lost = np.mean([r["orca"]["lost"] for sp, r in zip(specs, res) if sp.tag == f"{name}/{m}"])
            out.setdefault(name, {})[m] = dict(mean=float(v.mean()), ci=list(bootstrap_ci(v)), lost=float(lost), per_seed=v.tolist())
        from scipy.stats import wilcoxon
        a, b = np.array(out[name]["learned"]["per_seed"]), np.array(out[name]["rules"]["per_seed"])
        out[name]["p_learned_vs_rules"] = float(wilcoxon(a - b).pvalue) if len(a) >= 5 and not np.allclose(a, b) else None
    meta = json.loads((ARTIFACTS / f"reposition_value_metrics_{city}.json").read_text())
    res_all = dict(city=city, seeds=list(seeds), scenarios=out, start_hours=dict(scen), training=meta,
                   seconds=round(time.time() - t0))
    (DOCS / f"rl_{city}.json").write_text(json.dumps(res_all, indent=2))
    print(write_md(res_all))
    print(f"({time.time() - t0:.0f}s)")


def write_md(res: dict) -> str:
    city, seeds, out, meta = res["city"], res["seeds"], res["scenarios"], res["training"]
    md = ["# Learned repositioning", "",
          f"ORCA's idle cars repositioned three ways on real {city.capitalize()} demand, 45 simulated minutes from three "
          f"times of day, seeds {seeds[0]}-{seeds[-1]} (never used in training), paired; everything else identical.", "",
          "* **Rules**: the hand-written rebalancer: forecast demand per zone against idle cars, nearest surplus zone first.",
          "* **Learned**: a value network (zone embedding + live supply and forecast demand, 2 x 64 ReLU) trained by fitted "
          f"TD learning over {len(meta['iterations'])} rounds of policy iteration on seeds 201+ "
          f"({meta['iterations'][-1]['total']:,} transitions); cars move to the neighbouring zone whose value beats their "
          "own by more than the cost of driving there, with the same budget.",
          "* **None**: idle cars only cruise nearby.", "",
          "| Start | Rules | Learned | None | Share of the rules' gain the learned policy keeps | Learned vs rules (Wilcoxon p) |",
          "|---|---:|---:|---:|---:|---:|"]
    for name, h in res["start_hours"].items():
        o = out[name]
        p = o["p_learned_vs_rules"]
        gain = (o["off"]["mean"] - o["learned"]["mean"]) / max(1e-9, o["off"]["mean"] - o["rules"]["mean"])
        md.append(f"| {name} ({h:04.1f} h) | {o['rules']['mean']:.1f}s | {o['learned']['mean']:.1f}s "
                  f"({o['learned']['ci'][0]:.0f}-{o['learned']['ci'][1]:.0f}) | {o['off']['mean']:.1f}s | {gain:.0%} | "
                  f"{'n/a' if p is None else f'{p:.3f}'} |")
    md += ["", "Mean wait for a car; 95% bootstrap interval over seeds for the learned policy in brackets.", "",
           "## What this shows", "",
           "Repositioning matters: without it waits are 15-50% longer. The learned value policy recovers much of that, most "
           "at night and in the evening peak, but it does not beat the hand-written rules, which aim directly at riders' "
           "waits (forecast demand against idle cars) while the learned value is a car's expected rides. Two variants were "
           "tried and dropped: discounting the drive to the new zone (a repositioning car stays dispatchable, so the drive "
           "is not lost time; with it the policy almost never moved cars), and rewarding short pickups instead of rides "
           "(pickups are short wherever idle cars already gather, so the value pointed the wrong way). The rules stay the "
           "default; the learned policy runs with `rebalance=\"learned\"`."]
    text = "\n".join(md) + "\n"
    (DOCS / f"rl_{city}.md").write_text(text, encoding="utf-8", newline="\n")
    return text

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["train", "bench", "report"], help="report: rewrite the .md from the saved .json")
    ap.add_argument("--city", default="manhattan")
    ap.add_argument("--iters", type=int, default=4)
    ap.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 11)))
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args()
    if a.what == "train":
        train(a.city, a.iters, a.workers)
    elif a.what == "bench":
        bench(a.city, a.seeds, a.workers)
    else:
        print(write_md(json.loads((DOCS / f"rl_{a.city}.json").read_text())))
