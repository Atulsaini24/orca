"""Fleet state and the two dispatch policies.

* ``NearestDispatcher``: the common baseline. Every waiting request is offered,
  first come first served, to the idle unit closest in a straight line to its
  last reported GPS position.
* ``OrcaDispatcher``: every 3 s it scores (request, unit) pairs on predicted
  road ETA, learned acceptance probability and zone supply, then solves the
  batch as a min-cost assignment (Hungarian algorithm). It ignores units with
  stale GPS and pre-positions idle units toward zones forecast to run short.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from . import city as G
from . import config as C
from .city import clamp, zone_dist, zone_name, zone_of
from .features import FeatureStore
from .models import AcceptanceModel, DemandForecaster, EtaModel
from .world import Request, World

IDLE, OFFERED, PICKUP, TRIP = "idle", "offered", "pickup", "trip"


def hash2(a: int, b: int) -> float:
    """Deterministic uniform [0, 1) from two ints, so both policies see identical driver decisions."""
    h = (a * 0x9E3779B1 ^ (b + 0x632BE59B) * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 4294967296


class Agent:
    __slots__ = (
        "acc",
        "accepts",
        "cruise_wait",
        "hx",
        "hz",
        "id",
        "last_ping",
        "leg",
        "next_ping",
        "node",
        "offer_acc",
        "offer_at",
        "offer_deadline",
        "offers",
        "offline_until",
        "prev_node",
        "rebal",
        "ri",
        "route",
        "rs",
        "rx",
        "rz",
        "state",
        "x",
        "z",
    )

    def __init__(self, id_, x, z, acc, next_ping):
        self.id, self.x, self.z = id_, x, z
        self.hx, self.hz = 0.0, 1.0                 # unit heading vector
        self.node, self.prev_node = -1, -1         # street city: last intersection reached, and the one before
        self.state = IDLE
        self.acc = acc
        self.offline_until = -1.0
        self.last_ping = 0.0
        self.next_ping = next_ping
        self.rx, self.rz = x, z
        self.route: list = []
        self.ri = 0
        self.rs = None
        self.rebal = False
        self.offers = self.accepts = 0
        self.cruise_wait = 0.0
        self.offer_at = self.offer_deadline = 0.0
        self.offer_acc = False
        self.leg = None   # telemetry for the current pickup leg

    def online(self, t):
        return self.offline_until < t


def make_fleet(seed: int, n: int) -> list[tuple]:
    r = random.Random(seed * 7 + 1)
    city = G.current()
    out = []
    for i in range(n):
        x, z = city.random_point(r)
        acc = clamp(r.gauss(0.88, 0.07), 0.5, 0.99)
        if r.random() < 0.12:
            acc = 0.3 + r.random() * 0.2      # picky drivers
        out.append((i, x, z, acc, r.random() * 4))
    return out


@dataclass(slots=True)
class RequestState:
    r: Request
    status: str = "waiting"          # waiting | offered | assigned | ontrip | done | lost
    agent: Agent | None = None
    declined: set = field(default_factory=set)
    offers: int = 0
    assigned_at: float = -1.0
    picked_at: float = -1.0
    cands: list | None = None
    log: list = field(default_factory=list)       # timeline: (t, kind, car)


class Dispatcher:
    policy = "base"
    label = "Base"

    def __init__(self, world: World, fleet_spec: list[tuple], telemetry: list | None = None, ledger=None):
        self.world = world
        self.ledger = ledger
        n_zones = G.n_zones()
        self.city = G.current()
        self.z_sum = [0.0] * n_zones
        self.z_n = [0] * n_zones
        self.hist = [0] * 12                       # 30-second wait bands, last one is 5:30+
        self.timelines: dict[int, list] = {}
        self.tl_order: list[int] = []
        self.rng = random.Random(world.seed ^ 0xABC)
        self.agents = [Agent(*s) for s in fleet_spec]
        self.queue: list[RequestState] = []
        self.active: list[RequestState] = []
        self.fs = FeatureStore()
        self.telemetry = telemetry
        self.eta_true = EtaModel(self.fs, use_learned=False)   # used only to model driver behaviour
        self.last_outage = 0
        self.offline = 0
        self.batch_no = 0
        self.sample_timer = 0.0
        self.minute_timer = 0.0
        self.m = dict(served=0, lost=0, reoffers=0, timeouts=0, declines=0, waits=[], p1waits=[],
                      interval=[], interval_lost=0, series=[], all=[])
        self.B = 30.0
        self.BN = math.ceil(max(self.city.WX, self.city.WZ) / self.B) + 1
        self.buckets = [[] for _ in range(self.BN * self.BN)]

    # ---- main step ---------------------------------------------------------------
    def step(self, dt: float, new_reqs: list[Request]):
        w, t = self.world, self.world.t
        for o in w.outages:
            if o.id > self.last_outage:
                self.last_outage = o.id
                for a in self.agents:
                    if (a.x - o.x) ** 2 + (a.z - o.z) ** 2 < o.r * o.r:
                        a.offline_until = max(a.offline_until, o.until)
        offline = 0
        rnd = self.rng.random
        for a in self.agents:
            if a.offline_until < t and rnd() < 0.00003 * dt:
                a.offline_until = t + 120 + rnd() * 300
            online = a.offline_until < t
            if not online:
                offline += 1
            if online and t >= a.next_ping:
                if a.state in (PICKUP, TRIP):
                    self.fs.observe_ping(a.rx, a.rz, a.x, a.z, t - a.last_ping)
                a.rx, a.rz, a.last_ping = a.x, a.z, t
                a.next_ping = t + C.PING_MIN + rnd() * C.PING_JITTER
            if a.state == OFFERED:
                rs = a.rs
                if rs is None or rs.status != "offered" or rs.agent is not a:
                    a.state, a.rs = IDLE, None
                elif online and t >= a.offer_at:
                    self.offer_result(a, a.offer_acc)
                elif t >= a.offer_deadline:
                    self.m["timeouts"] += 1
                    a.rs.log.append((t, "expire", a.id))
                    self.release(a, a.rs)
                continue
            self.move(a, dt)
            if a.ri >= len(a.route):
                if a.state == IDLE:
                    a.rebal = False
                    a.cruise_wait -= dt
                    if a.cruise_wait <= 0:
                        self.cruise(a)
                        a.cruise_wait = 10 + rnd() * 30
                else:
                    self.arrive(a)
        self.offline = offline

        for r in new_reqs:
            rs = RequestState(r)
            rs.log.append((t, "request", None))
            if self.policy == "smart":
                self.timelines[r.id] = rs.log
                self.tl_order.append(r.id)
                if len(self.tl_order) > 1500:
                    self.timelines.pop(self.tl_order.pop(0), None)
            self.queue.append(rs)
            self.active.append(rs)
            self.fs.observe_request(r.zone)

        for rs in self.active:
            if rs.status in ("waiting", "offered", "assigned") and t - rs.r.t > C.ABANDON:
                self.lose(rs)
        self.active = [rs for rs in self.active if rs.status in ("waiting", "offered", "assigned")]

        self.minute_timer += dt
        if self.minute_timer >= 60:
            self.minute_timer -= 60
            self.fs.roll_minute()
            self.on_minute()

        self.dispatch(dt)

        self.sample_timer += dt
        if self.sample_timer >= 60:
            self.sample_timer -= 60
            iv = self.m["interval"]
            self.m["series"].append((t, sum(iv) / len(iv) if iv else None, self.m["interval_lost"]))
            if len(self.m["series"]) > 120:
                self.m["series"].pop(0)
            self.m["interval"], self.m["interval_lost"] = [], 0

    def on_minute(self):
        pass

    def dispatch(self, dt):
        raise NotImplementedError

    # ---- movement ------------------------------------------------------------------
    def move(self, a: Agent, dt: float):
        if a.ri >= len(a.route):
            return
        rem = self.city.base_speed * self.world.congestion(a.x, a.z) * dt * (C.IDLE_CRUISE if a.state == IDLE and not a.rebal else 1)
        guard = 0
        while rem > 0 and a.ri < len(a.route) and guard < 64:
            guard += 1
            wx, wz, mult, node = a.route[a.ri]
            dx, dz = wx - a.x, wz - a.z
            d = math.hypot(dx, dz)
            step = rem * mult
            if d > 1e-9:
                a.hx, a.hz = dx / d, dz / d
            if d <= step:
                a.x, a.z = wx, wz
                rem -= d / mult
                a.ri += 1
                if node >= 0:
                    a.prev_node, a.node = a.node, node
            else:
                a.x += dx / d * step
                a.z += dz / d * step
                rem = 0

    def route_to(self, a: Agent, tx: float, tz: float):
        a.route = self.city.route(a, tx, tz)
        a.ri = 0

    def cruise(self, a: Agent):
        a.route = self.city.cruise(a, self.rng)
        a.ri = 0

    def arrive(self, a: Agent):
        t, rs = self.world.t, a.rs
        if a.state == PICKUP:
            rs.status, rs.picked_at = "ontrip", t
            wait = t - rs.r.t
            m = self.m
            m["served"] += 1
            m["waits"].append(wait)
            if len(m["waits"]) > 500:
                m["waits"].pop(0)
            if rs.r.p1:
                m["p1waits"].append(wait)
                if len(m["p1waits"]) > 150:
                    m["p1waits"].pop(0)
            m["interval"].append(wait)
            m["all"].append(wait)
            self.z_sum[rs.r.zone] += wait
            self.z_n[rs.r.zone] += 1
            self.hist[min(11, int(wait // 30))] += 1
            rs.log.append((t, "pickup", a.id))
            if self.ledger is not None:
                self.ledger.pickup(self.policy, rs.r, t, wait, a.id, rs.offers)
            if a.leg is not None and self.telemetry is not None:
                feats, t0 = a.leg
                self.telemetry.append((*feats, t - t0))
            a.leg = None
            a.state = TRIP
            self.route_to(a, rs.r.dx, rs.r.dz)
        elif a.state == TRIP:
            rs.status = "done"
            a.rs, a.state, a.route, a.ri = None, IDLE, [], 0
            a.cruise_wait = 5 + self.rng.random() * 20

    # ---- offers ----------------------------------------------------------------------
    def lose(self, rs: RequestState):
        a = rs.agent
        if a is not None and a.rs is rs:
            a.state, a.rs, a.route, a.ri, a.cruise_wait, a.leg = IDLE, None, [], 0, 2.0, None
        rs.status, rs.agent = "lost", None
        rs.log.append((self.world.t, "cancel", None))
        if self.ledger is not None:
            self.ledger.lost(self.policy, rs.r, self.world.t)
        self.m["lost"] += 1
        self.m["interval_lost"] += 1

    def true_accept_p(self, a: Agent, rs: RequestState) -> float:
        d = abs(rs.r.x - a.x) + abs(rs.r.z - a.z)
        eta = d / (C.BASE_SPEED * 0.8) + 4
        return clamp(a.acc - 0.03 * max(0.0, eta / 60 - 4) + (0.06 if rs.r.p1 else 0), 0.03, 0.995)

    def offer(self, a: Agent, rs: RequestState):
        t = self.world.t
        a.state, a.rs, a.route, a.ri, a.rebal = OFFERED, rs, [], 0, False
        rs.status, rs.agent = "offered", a
        rs.log.append((t, "offer", a.id))
        rs.offers += 1
        a.offers += 1
        a.offer_acc = hash2(a.id * 131 + 7, rs.r.id * 7 + rs.offers) < self.true_accept_p(a, rs)
        a.offer_at = t + 4 + hash2(a.id, rs.r.id) * 7
        a.offer_deadline = t + C.OFFER_TIMEOUT

    def offer_result(self, a: Agent, accepted: bool):
        rs, t = a.rs, self.world.t
        if accepted:
            a.accepts += 1
            a.state, rs.status, rs.assigned_at = PICKUP, "assigned", t
            rs.log.append((t, "accept", a.id))
            if self.telemetry is not None:
                a.leg = (self.fs.eta_features(a.x, a.z, rs.r.x, rs.r.z, self.world.clock()), t)
            self.route_to(a, rs.r.x, rs.r.z)
        else:
            self.m["declines"] += 1
            rs.log.append((t, "decline", a.id))
            self.release(a, rs)

    def release(self, a: Agent, rs: RequestState):
        self.m["reoffers"] += 1
        rs.declined.add(a.id)
        rs.status, rs.agent = "waiting", None
        self.queue.append(rs)
        a.state, a.rs, a.cruise_wait = IDLE, None, 3.0

    # ---- spatial index ------------------------------------------------------------------
    def build_index(self, fresh_only: bool):
        t, B, BN = self.world.t, self.B, self.BN
        for b in self.buckets:
            b.clear()
        for a in self.agents:
            if a.state != IDLE or (fresh_only and t - a.last_ping > C.STALE_AFTER):
                continue
            bx = clamp(int(a.rx // B), 0, BN - 1)
            bz = clamp(int(a.rz // B), 0, BN - 1)
            self.buckets[bz * BN + bx].append(a)

    def k_nearest(self, x, z, k, excluded: set) -> list[Agent]:
        B, BN = self.B, self.BN
        cx, cz = clamp(int(x // B), 0, BN - 1), clamp(int(z // B), 0, BN - 1)
        found, stop_at = [], -1
        for r in range(BN):
            for bz in range(cz - r, cz + r + 1):
                if bz < 0 or bz >= BN:
                    continue
                for bx in range(cx - r, cx + r + 1):
                    if bx < 0 or bx >= BN or max(abs(bx - cx), abs(bz - cz)) != r:
                        continue
                    for a in self.buckets[bz * BN + bx]:
                        if a.state == IDLE and a.id not in excluded:
                            found.append(((a.rx - x) ** 2 + (a.rz - z) ** 2, a.id, a))
            if len(found) >= k:
                if stop_at < 0:
                    stop_at = r + 1
                if r >= stop_at:
                    break
        found.sort(key=lambda f: (f[0], f[1]))
        return [f[2] for f in found[:k]]

    # ---- reporting -------------------------------------------------------------------------
    def zone_means(self) -> list:
        return [round(s / n, 1) if n else None for s, n in zip(self.z_sum, self.z_n)]

    def timeline(self, req_id: int) -> list | None:
        log = self.timelines.get(req_id)
        if log is None:
            return None
        return [dict(t=round(t, 1), clock=C.START_CLOCK + t, k=k, car=car) for t, k, car in log]

    def stats(self) -> dict:
        s = sorted(self.m["waits"])
        t = self.world.t
        pct = lambda q: s[min(len(s) - 1, int(q * len(s)))] if s else 0.0
        p1 = self.m["p1waits"]
        busy = sum(1 for a in self.agents if a.state != IDLE)
        off = sum(1 for a in self.agents if a.offline_until >= t)
        waiting = sum(1 for rs in self.active if rs.status in ("waiting", "offered"))
        return dict(mean=sum(s) / len(s) if s else 0.0, p50=pct(0.5), p95=pct(0.95),
                    p1=sum(p1) / len(p1) if p1 else 0.0, served=self.m["served"], lost=self.m["lost"],
                    reoffers=self.m["reoffers"], timeouts=self.m["timeouts"], util=busy / len(self.agents),
                    offline=off, waiting=waiting)


class NearestDispatcher(Dispatcher):
    policy = "base"
    label = "Nearest unit"

    def dispatch(self, dt):
        q = [rs for rs in self.queue if rs.status == "waiting"]
        self.queue = []
        if not q:
            return
        self.build_index(fresh_only=False)
        for rs in q:
            best = self.k_nearest(rs.r.x, rs.r.z, 1, rs.declined)
            if best:
                self.offer(best[0], rs)
            else:
                self.queue.append(rs)


def deadline_cost(wait: float, eta: float) -> float:
    """Penalty for a pairing that would reach the rider close to or after the moment they give up.

    Riders cancel after C.ABANDON seconds. A logistic ramp over the last two minutes before that point
    makes ORCA prefer, for riders who have waited long, a car that arrives in time over a cheaper one
    that would arrive too late; without it a 16-minute arrival scores almost like a 14-minute one.
    """
    if C.DEADLINE_W <= 0:
        return 0.0
    slack = C.ABANDON - (wait + eta + 30.0)                # 30 s margin for the offer round-trip
    return C.DEADLINE_W * C.DEADLINE_COST / (1.0 + math.exp(slack / 40.0))


class OrcaDispatcher(Dispatcher):
    policy = "smart"
    label = "ORCA"

    def __init__(self, world, fleet_spec, telemetry=None, use_learned=True, ledger=None):
        super().__init__(world, fleet_spec, telemetry, ledger)
        self.eta = EtaModel(self.fs, use_learned)
        self.accept = AcceptanceModel()
        self.forecaster = DemandForecaster(self.fs, use_learned, "tlc" if world.source.name == "nyc-tlc" else "sim")
        self.z_idle = [0] * G.n_zones()
        self.deficit_zones: list[int] = []
        self.batch_timer = 0.0
        self.rebal_timer = 0.0
        self.last_rebal_log = -1e9
        self.explain: dict[int, dict] = {}
        self.explain_order: list[int] = []
        self.notable: dict | None = None
        self.solver_used = "hungarian"
        self.solve_ms = 0.0

    def on_minute(self):
        self.forecaster.update(self.world.clock())

    def dispatch(self, dt):
        self.batch_timer += dt
        if self.batch_timer >= C.BATCH:
            self.batch_timer -= C.BATCH
            self.match()
        self.rebal_timer += dt
        if self.rebal_timer >= C.REBAL:
            self.rebal_timer -= C.REBAL
            self.rebalance()

    def compute_zone_idle(self):
        t = self.world.t
        self.z_idle = [0] * len(self.z_idle)
        for a in self.agents:
            if a.state == IDLE and t - a.last_ping <= C.STALE_AFTER:
                self.z_idle[zone_of(a.rx, a.rz)] += 1

    def zone_cost(self, x, z) -> float:
        zi = zone_of(x, z)
        ratio = self.forecaster.forecast[zi] * C.COVER / (self.z_idle[zi] + 1)
        return min(C.ZONE_CAP, (ratio - 1) * C.ZONE_SLOPE) if ratio > 1 else 0.0

    def match(self):
        import time as _time
        t, clock = self.world.t, self.world.clock()
        self.batch_no += 1
        q = [rs for rs in self.queue if rs.status == "waiting"]
        self.queue = []
        if not q:
            return
        self.build_index(fresh_only=True)
        self.compute_zone_idle()
        t0 = _time.perf_counter()

        cand_lists = [self.k_nearest(rs.r.x, rs.r.z, C.K, rs.declined) for rs in q]
        pairs = [(a.rx, a.rz, rs.r.x, rs.r.z) for rs, cl in zip(q, cand_lists) for a in cl]
        etas = self.eta.predict_many(pairs, clock)
        k = 0
        for rs, cl in zip(q, cand_lists):
            wait = t - rs.r.t
            rs.cands = []
            for a in cl:
                eta = float(etas[k]); k += 1
                p = self.accept.p(a, eta, rs.r.p1)
                zc = self.zone_cost(a.rx, a.rz)
                cost = eta + (1 - p) * C.REOFFER + zc
                adj = cost * (C.URGENT_W if rs.r.p1 else 1.0) - min(wait, C.WAIT_CAP) * C.WAIT_W + deadline_cost(wait, eta)
                rs.cands.append(dict(a=a, eta=eta, p=p, zc=zc, cost=cost, adj=adj, age=t - a.last_ping, rx=a.rx, rz=a.rz))

        assignments = self.solve(q)
        self.solve_ms = (_time.perf_counter() - t0) * 1000
        for rs, c in assignments:
            self.offer(c["a"], rs)
            self.record_explain(rs, c)
        for rs in q:
            rs.cands = None
            if rs.status == "waiting":
                self.queue.append(rs)

    def solve(self, q):
        """Min-cost assignment of requests to units for this batch."""
        if len(q) <= C.HUNGARIAN_MAX:
            self.solver_used = "hungarian"
            units = {}
            for rs in q:
                for c in rs.cands:
                    units.setdefault(c["a"].id, len(units))
            if not units:
                return []
            BIG = 1e6
            M = np.full((len(q), len(units)), BIG)
            lookup = {}
            for i, rs in enumerate(q):
                for c in rs.cands:
                    j = units[c["a"].id]
                    M[i, j] = c["adj"] + 400   # shift so every real pair is positive and preferred over none
                    lookup[(i, j)] = c
            rows, cols = linear_sum_assignment(M)
            return [(q[i], lookup[(i, j)]) for i, j in zip(rows, cols) if M[i, j] < BIG]
        # Overload: greedy on sorted pair costs (O(n log n)), oldest and P1 requests first.
        self.solver_used = "greedy (overload)"
        pairs = sorted(((c["adj"], i, c) for i, rs in enumerate(q) for c in rs.cands), key=lambda p: (p[0], p[1]))
        taken, done, out = set(), set(), []
        for _, i, c in pairs:
            if i in done or c["a"].id in taken:
                continue
            taken.add(c["a"].id)
            done.add(i)
            out.append((q[i], c))
        return out

    def record_explain(self, rs: RequestState, chosen: dict):
        t, r = self.world.t, rs.r
        lst = sorted(rs.cands, key=lambda c: c["cost"])[:5]
        if chosen not in lst:
            lst[-1] = chosen
        best, bd = None, math.inf
        for a in self.agents:
            if not (a.state == IDLE or a is chosen["a"]) or a.id in rs.declined:
                continue
            d = (a.rx - r.x) ** 2 + (a.rz - r.z) ** 2
            if d < bd:
                bd, best = d, a
        base = None
        if best is not None:
            eta = float(self.eta.predict_many([(best.rx, best.rz, r.x, r.z)], self.world.clock())[0])
            p = self.accept.p(best, eta, r.p1)
            zc = self.zone_cost(best.rx, best.rz)
            age = t - best.last_ping
            same = best is chosen["a"]
            if same:
                reason = "Same unit. Both policies agree here."
            elif age > C.STALE_AFTER:
                reason = f"Its GPS was last seen {round(age)}s ago, so it is probably unreachable and the offer would hang until it times out."
            elif p < chosen["p"] - 0.12:
                reason = f"Its driver accepts only about {round(p * 100)}% of offers. A decline costs about {int(C.REOFFER)}s of re-offering."
            elif eta > chosen["eta"] + 8:
                reason = f"It is closer in a straight line, but {round(eta - chosen['eta'])}s slower by road and traffic."
            elif zc > 5:
                reason = f"It is one of the last idle cars in {zone_name(zone_of(best.rx, best.rz))}, where demand is about to rise."
            else:
                reason = "It is the nearest unit, but the batch is cheaper overall with it serving another request."
            base = dict(id=best.id, eta=eta, p=p, zc=zc, cost=eta + (1 - p) * C.REOFFER + zc, age=age,
                        rx=best.rx, rz=best.rz, reason=reason, same=same)
        rec = dict(
            id=r.id, clock=self.world.clock(), p1=r.p1, zone=r.zone, zone_name=zone_name(r.zone), batch=self.batch_no,
            x=r.x, z=r.z, wait=t - r.t, attempt=rs.offers, solver=self.solver_used,
            chosen={k: chosen[k] for k in ("eta", "p", "zc", "cost", "age", "rx", "rz")} | {"id": chosen["a"].id},
            cands=[{k: c[k] for k in ("eta", "p", "zc", "cost", "age", "rx", "rz")} |
                   {"id": c["a"].id, "chosen": c is chosen, "taken": c is not chosen and c["a"].state != IDLE} for c in lst],
            base=base,
        )
        self.explain[r.id] = rec
        self.explain_order.append(r.id)
        if len(self.explain_order) > 800:
            self.explain.pop(self.explain_order.pop(0), None)
        if base and not base["same"] and (r.p1 or base["age"] > C.STALE_AFTER or self.rng.random() < 0.25):
            self.notable = rec

    def rebalance(self):
        t, N = self.world.t, G.n_zones()
        self.compute_zone_idle()
        fc = self.forecaster.forecast
        deficit, surplus = [], {}
        for i in range(N):
            d = fc[i] * C.COVER - self.z_idle[i]
            if d > 1.5:
                deficit.append((i, d))
            elif -d > 2:
                surplus[i] = int(-d)
        deficit.sort(key=lambda x: -x[1])
        self.deficit_zones = [d[0] for d in deficit[:8]]
        by_zone = [[] for _ in range(N)]
        for a in self.agents:
            if a.state == IDLE and not a.rebal and t - a.last_ping <= C.STALE_AFTER:
                by_zone[zone_of(a.rx, a.rz)].append(a)
        budget, moved = math.ceil(len(self.agents) * 0.015), 0
        for zi, d in deficit:
            want = min(math.ceil(d), budget)
            for sz in sorted((k for k, v in surplus.items() if v > 0 and zone_dist(k, zi) < 2.9), key=lambda k: zone_dist(k, zi)):
                pool = by_zone[sz]
                while want > 0 and surplus[sz] > 0 and pool:
                    a = pool.pop()
                    self.route_to(a, *self.city.point_in_zone(zi, self.rng))
                    a.rebal, a.cruise_wait = True, 0.0
                    want -= 1; budget -= 1; moved += 1; surplus[sz] -= 1
                if want <= 0:
                    break
            if budget <= 0:
                break
        if moved >= 12 and t - self.last_rebal_log > 420 and deficit:
            self.last_rebal_log = t
            self.world.log("rebal", f"ORCA moved {moved} idle cars toward "
                                    + ", ".join(zone_name(d[0]) for d in deficit[:3]) + " ahead of forecast demand")
