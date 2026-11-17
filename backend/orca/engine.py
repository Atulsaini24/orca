"""The engine: one world, two dispatchers on identical inputs, plus snapshots for clients."""
from __future__ import annotations

import math
import time

import numpy as np

from . import config as C
from .city import zone_name
from .dispatch import IDLE, NearestDispatcher, OrcaDispatcher, make_fleet
from .world import World

# client car codes: 0 idle, 1 repositioning, 2 offer sent, 3 to pickup, 4 rider on board, 5 no GPS,
# 6 driving to a charger, 7 charging; indexed here by fleet state (idle, offered, pickup, trip, charge, plugged)
STATE_CODE = np.array([0, 2, 3, 4, 6, 7])
REQ_CODE = {"waiting": 0, "offered": 1, "assigned": 2}


class Engine:
    def __init__(self, seed: int = 7, fleet: int = 1200, demand: str = "synthetic", data: str | None = None,
                 zones: str | None = None, use_learned: bool = True, telemetry: bool = False, db: str = ":memory:",
                 city: str | None = None, pool: bool = False, demand_fleet: int | None = None, ev: str | None = None,
                 rebalance: str = "rules", rl_net: str | None = None, rl_explore: float = 0.0, rl_log: bool = False):
        from . import city as G
        self.city = G.use(city)
        self.config = dict(seed=seed, fleet=fleet, demand=demand, data=data, zones=zones, use_learned=use_learned,
                           city=self.city.name, pool=pool, ev=ev, rebalance=rebalance)
        tlc = None
        if demand == "tlc":
            from .demand import TLCReplay
            tlc = TLCReplay(data, zones)
        self.world = World(seed, fleet, demand, tlc, demand_fleet=demand_fleet)
        spec = make_fleet(seed, fleet)
        self.telemetry = [] if telemetry else None
        from .ledger import Ledger
        self.ledger = Ledger(db, run=f"seed{seed}-fleet{fleet}-{int(time.time())}")
        # ev: None (petrol), "smart" (ORCA plans charging) or "naive" (ORCA dispatches, drivers charge as they like);
        # nearest-car dispatch always leaves charging to the drivers
        self.smart = OrcaDispatcher(self.world, spec, self.telemetry, use_learned, self.ledger, pool=pool, ev=ev,
                                    rebalance=rebalance, rl_net=rl_net, rl_explore=rl_explore, rl_log=rl_log)
        self.base = NearestDispatcher(self.world, spec, ledger=self.ledger, ev="naive" if ev else None)
        self.speed = 20.0
        self.paused = False
        self.step_ms = 0.0
        self.world.log("sys", f"{self.city.label}: {fleet:,} cars per dispatcher. Demand: {self.demand_label()}. "
                              f"ETA model: {self.smart.eta.kind}. Forecast: {self.smart.forecaster.kind}."
                              + (f" Shared rides on: {C.POOL_OPT_IN:.0%} of riders accept sharing, up to {C.POOL_CAP} per car."
                                 if pool else ""))

    def demand_label(self):
        src = self.world.source
        if src.name == "nyc-tlc":
            from . import cityspec
            spec = cityspec.CITIES.get(self.city.name)
            return f"real {spec.demand_source}" if spec else "real trip records"
        return "synthetic city demand"

    # ---- stepping ---------------------------------------------------------------
    def step(self, dt: float):
        reqs = self.world.step(dt)
        self.smart.step(dt, reqs)
        self.base.step(dt, reqs)

    def advance(self, real_dt: float):
        """Advance by wall-clock time scaled by speed, in sub-steps of at most 1 sim-second."""
        if self.paused:
            return
        t0 = time.perf_counter()
        sim_dt = min(real_dt, 0.25) * self.speed
        while sim_dt > 1e-6:
            d = min(1.0, sim_dt)
            self.step(d)
            sim_dt -= d
        self.step_ms = (time.perf_counter() - t0) * 1000

    def warm_up(self, seconds: int = 540):
        for _ in range(seconds):
            self.step(1.0)

    # ---- commands ----------------------------------------------------------------
    def command(self, cmd: dict) -> dict:
        if not isinstance(cmd, dict):
            return {"ok": False, "error": "Send a JSON object like {\"cmd\": \"spike\"}"}
        c = cmd.get("cmd")
        w = self.world

        def number(default, lo, hi):
            v = cmd.get("value", default)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                raise ValueError
            return float(max(lo, min(hi, v)))
        try:
            if c == "speed":
                self.speed = number(20, 1, 120)
                return {"ok": True}
            if c == "demand":
                w.demand_mult = number(1, 0.3, 2.0)
                return {"ok": True}
        except ValueError:
            return {"ok": False, "error": f"'{c}' needs a numeric value"}
        if c == "pause":
            v = cmd.get("value", not self.paused)
            if not isinstance(v, bool):
                return {"ok": False, "error": "'pause' needs true or false"}
            self.paused = v
        elif c == "spike":
            w.trigger_spike()
        elif c == "outage":
            w.trigger_outage()
        elif c == "accident":
            w.trigger_accident()
        else:
            return {"ok": False, "error": f"Unknown command '{c}'"}
        return {"ok": True}

    # ---- snapshots ------------------------------------------------------------------
    def disp(self, view: str):
        return self.smart if view == "smart" else self.base

    def frame(self, view: str) -> dict:
        """Fast-changing state, sent about 10 times a second."""
        d, t = self.disp(view), self.world.t
        f = d.f
        code = STATE_CODE[f.state]
        code[(f.state == IDLE) & f.rebal] = 1
        code[f.offline_until >= t] = 5
        heading = np.rint(np.arctan2(f.hx, f.hz) / math.tau * 256).astype(np.int64) & 255   # 0-255 = full turn
        agents = dict(x=np.round(f.x, 2).tolist(), z=np.round(f.z, 2).tolist(), h=heading.tolist(), s=code.tolist(),
                      tx=np.round(f.tgt_x, 2).tolist(), tz=np.round(f.tgt_z, 2).tolist())
        if d.pool:
            agents["o"] = f.load.tolist()                  # riders on board
        if d.ev is not None:
            agents["b"] = np.clip(np.rint(f.soc * 100), 0, 100).astype(np.int64).tolist()    # battery, %
        reqs = [[rs.r.id, rs.r.x, rs.r.z, REQ_CODE.get(rs.status, 0), 1 if rs.r.p1 else 0, round(t - rs.r.t, 1)]
                for rs in d.active]
        return dict(type="frame", t=round(t, 2), clock=self.world.clock(), view=view, agents=agents, reqs=reqs)

    def summary(self) -> dict:
        """Slow-changing state, sent about once a second."""
        w, s = self.world, self.smart
        fc = s.forecaster.forecast
        top = sorted(range(len(fc)), key=lambda i: -fc[i])[:5]
        fx = dict(chargers=dict(smart=s.ev.snapshot(), base=self.base.ev.snapshot()) if s.ev is not None else None,
                  outages=[[o.x, o.z, o.r] for o in w.outages],
                  accidents=[[b.x, b.z, b.r] for b in w.blobs if b.accident],
                  spikes=[[h.x, h.z, h.s * 1.6] for h in w.spikes])
        return dict(
            type="summary", t=w.t, clock=w.clock(), rate=w.rate(), batch=s.batch_no,
            stats=dict(smart=self.smart.stats(), base=self.base.stats()),
            series=dict(smart=[[p[0], p[1]] for p in self.smart.m["series"]],
                        base=[[p[0], p[1]] for p in self.base.m["series"]]),
            forecast=[dict(zone=i, name=zone_name(i), forecast=float(fc[i]), need=float(fc[i]) * C.COVER, idle=int(s.z_idle[i]))
                      for i in top],
            deficit=s.deficit_zones, fx=fx,
            heat=[min(255, int(v * 10)) for v in w.heat], heat_scale=10, heat_w=w.HEAT_W, heat_h=w.HEAT_H,
            notable=(s.notable | {"timeline": s.timeline(s.notable["id"])}) if s.notable else None,
            zones=dict(smart=s.zone_means(), base=self.base.zone_means()), hist=dict(smart=s.hist, base=self.base.hist),
            events=[dict(seq=e.seq, clock=e.clock, type=e.type, msg=e.msg) for e in w.events[-40:]],
            engine=dict(speed=self.speed, paused=self.paused, step_ms=round(self.step_ms, 2), solver=s.solver_used,
                        solve_ms=round(s.solve_ms, 2), eta_model=s.eta.kind, forecaster=s.forecaster.kind,
                        demand=self.demand_label(), fleet=s.n, city=self.city.name, pool=s.pool),
        )

    def explain(self, req_id: int) -> dict:
        rec = self.smart.explain.get(req_id)
        timeline = self.smart.timeline(req_id)
        if rec:
            return dict(found=True, rec=rec | {"timeline": timeline})
        for rs in self.smart.active:
            if rs.r.id == req_id:
                return dict(found=False, waiting=True, id=req_id, p1=rs.r.p1, zone_name=zone_name(rs.r.zone),
                            wait=self.world.t - rs.r.t, timeline=timeline)
        return dict(found=False, waiting=False, id=req_id)

    def models(self) -> dict:
        """Training metrics of the models in use, plus the latest benchmark, for the model report."""
        import json
        from pathlib import Path

        from .models import artifact
        docs = Path(__file__).resolve().parents[2] / "docs"
        read = lambda p: json.loads(p.read_text()) if p.exists() else None
        src = "tlc" if self.world.source.name == "nyc-tlc" else "sim"
        return dict(eta=read(artifact("eta_metrics", "json")), demand=read(artifact(f"demand_{src}_metrics", "json")),
                    benchmark=read(docs / "benchmark.json"), in_use=dict(eta=self.smart.eta.kind, demand=self.smart.forecaster.kind))

    def prometheus(self) -> str:
        lines = []
        for name, d in (("orca", self.smart), ("nearest", self.base)):
            st = d.stats()
            for k in ("mean", "p50", "p95", "p1"):
                lines.append(f'orca_wait_seconds{{policy="{name}",stat="{k}"}} {st[k]:.3f}')
            lines.append(f'orca_requests_served_total{{policy="{name}"}} {st["served"]}')
            lines.append(f'orca_requests_lost_total{{policy="{name}"}} {st["lost"]}')
            lines.append(f'orca_reoffers_total{{policy="{name}"}} {st["reoffers"]}')
            lines.append(f'orca_fleet_utilization{{policy="{name}"}} {st["util"]:.4f}')
            lines.append(f'orca_queue_length{{policy="{name}"}} {st["waiting"]}')
        lines.append(f"orca_match_solve_ms {self.smart.solve_ms:.3f}")
        lines.append(f"orca_engine_step_ms {self.step_ms:.3f}")
        lines.append(f"orca_demand_rate_per_s {self.world.rate():.4f}")
        return "\n".join(lines) + "\n"
