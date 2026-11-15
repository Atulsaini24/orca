"""The engine: one world, two dispatchers on identical inputs, plus snapshots for clients."""
from __future__ import annotations

import time

from . import config as C
from .city import zone_name
from .dispatch import IDLE, NearestDispatcher, OrcaDispatcher, make_fleet
from .world import World

STATE_CODE = {"idle": 0, "offered": 2, "pickup": 3, "trip": 4}
REQ_CODE = {"waiting": 0, "offered": 1, "assigned": 2}


class Engine:
    def __init__(self, seed: int = 7, fleet: int = 1200, demand: str = "synthetic", data: str | None = None,
                 zones: str | None = None, use_learned: bool = True, telemetry: bool = False, db: str = ":memory:"):
        self.config = dict(seed=seed, fleet=fleet, demand=demand, data=data, zones=zones, use_learned=use_learned)
        tlc = None
        if demand == "tlc":
            from .demand import TLCReplay
            tlc = TLCReplay(data, zones)
        self.world = World(seed, fleet, demand, tlc)
        spec = make_fleet(seed, fleet)
        self.telemetry = [] if telemetry else None
        from .ledger import Ledger
        self.ledger = Ledger(db, run=f"seed{seed}-fleet{fleet}-{int(time.time())}")
        self.smart = OrcaDispatcher(self.world, spec, self.telemetry, use_learned, self.ledger)
        self.base = NearestDispatcher(self.world, spec, ledger=self.ledger)
        self.speed = 20.0
        self.paused = False
        self.step_ms = 0.0
        self.world.log("sys", f"City online with {fleet:,} cars per dispatcher. Demand: {self.demand_label()}. "
                              f"ETA model: {self.smart.eta.kind}. Forecast: {self.smart.forecaster.kind}.")

    def demand_label(self):
        src = self.world.source
        if src.name == "nyc-tlc":
            return "real NYC TLC taxi trips"
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
        xs, zs, hs, ss, tx, tz = [], [], [], [], [], []
        for a in d.agents:
            xs.append(round(a.x, 2)); zs.append(round(a.z, 2))
            hs.append(0 if a.hz > 0 else 1 if a.hx > 0 else 2 if a.hz < 0 else 3)
            if a.offline_until >= t:
                code = 5
            elif a.state == IDLE:
                code = 1 if a.rebal else 0
            else:
                code = STATE_CODE[a.state]
            ss.append(code)
            if a.rs is not None and a.state != IDLE:
                r = a.rs.r
                tx.append(r.dx if a.state == "trip" else r.x); tz.append(r.dz if a.state == "trip" else r.z)
            else:
                tx.append(-1); tz.append(-1)
        reqs = [[rs.r.id, rs.r.x, rs.r.z, REQ_CODE.get(rs.status, 0), 1 if rs.r.p1 else 0, round(t - rs.r.t, 1)]
                for rs in d.active]
        return dict(type="frame", t=round(t, 2), clock=self.world.clock(), view=view,
                    agents=dict(x=xs, z=zs, h=hs, s=ss, tx=tx, tz=tz), reqs=reqs)

    def summary(self) -> dict:
        """Slow-changing state, sent about once a second."""
        w, s = self.world, self.smart
        fc = s.forecaster.forecast
        top = sorted(range(len(fc)), key=lambda i: -fc[i])[:5]
        fx = dict(outages=[[o.x, o.z, o.r] for o in w.outages],
                  accidents=[[b.x, b.z, b.r] for b in w.blobs if b.accident],
                  spikes=[[h.x, h.z, h.s * 1.6] for h in w.spikes])
        return dict(
            type="summary", t=w.t, clock=w.clock(), rate=w.rate(), batch=s.batch_no,
            stats=dict(smart=self.smart.stats(), base=self.base.stats()),
            series=dict(smart=[[p[0], p[1]] for p in self.smart.m["series"]],
                        base=[[p[0], p[1]] for p in self.base.m["series"]]),
            forecast=[dict(zone=i, name=zone_name(i), forecast=fc[i], need=fc[i] * C.COVER, idle=s.z_idle[i]) for i in top],
            deficit=s.deficit_zones, fx=fx,
            heat=[min(255, int(v * 10)) for v in w.heat], heat_scale=10,
            notable=(s.notable | {"timeline": s.timeline(s.notable["id"])}) if s.notable else None,
            zones=dict(smart=s.zone_means(), base=self.base.zone_means()), hist=dict(smart=s.hist, base=self.base.hist),
            events=[dict(seq=e.seq, clock=e.clock, type=e.type, msg=e.msg) for e in w.events[-40:]],
            engine=dict(speed=self.speed, paused=self.paused, step_ms=round(self.step_ms, 2), solver=s.solver_used,
                        solve_ms=round(s.solve_ms, 2), eta_model=s.eta.kind, forecaster=s.forecaster.kind,
                        demand=self.demand_label(), fleet=len(s.agents)),
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

        from .models import ARTIFACTS
        docs = Path(__file__).resolve().parents[2] / "docs"
        read = lambda p: json.loads(p.read_text()) if p.exists() else None
        src = "tlc" if self.world.source.name == "nyc-tlc" else "sim"
        return dict(eta=read(ARTIFACTS / "eta_metrics.json"), demand=read(ARTIFACTS / f"demand_{src}_metrics.json"),
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
