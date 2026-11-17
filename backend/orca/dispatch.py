"""Fleet state and the dispatch policies.

* ``NearestDispatcher``: the common baseline. Every waiting request is offered, first come first
  served, to the idle car closest in a straight line to its last reported GPS position.
* ``OrcaDispatcher``: every 3 s it scores (request, car) pairs on predicted road ETA, learned
  acceptance probability and zone supply, then solves the batch as an exact min-cost assignment
  (dense Hungarian for small batches, sparse LAPJV for large ones). It ignores cars with stale GPS and pre-positions idle cars toward zones
  forecast to run short. With ``pool=True`` it also offers shared rides: a rider who opts in can be
  inserted into a car already carrying or fetching others, at the cheapest feasible point of its
  plan (kernels.best_insertion), competing in the same assignment with the idle cars.

The fleet is a structure of arrays (``Fleet``): positions, states, routes and odometers are NumPy
arrays advanced each tick by one compiled kernel (kernels.step_fleet). Python handles only the
events: offers, answers, arrivals at stops, cruising and rebalancing.

Each car follows an ordered list of stops (pick up rider A, pick up rider B, drop A, drop B, ...).
Without pooling that list is just [pickup, drop-off].
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import min_weight_full_bipartite_matching
from scipy.special import expit

from . import city as G
from . import config as C
from . import kernels as K
from .city import clamp, zone_dist, zone_name
from .features import FeatureStore
from .models import AcceptanceModel, DemandForecaster, EtaModel
from .world import Request, World, hash2

IDLE, OFFERED, PICKUP, TRIP, CHARGE, PLUGGED, OFF = (K.S_IDLE, K.S_OFFERED, K.S_PICKUP, K.S_TRIP, K.S_CHARGE,
                                                    K.S_PLUGGED, K.S_OFF)
STATE_NAMES = ("idle", "offered", "pickup", "trip", "charge", "plugged", "off duty")
PICK, DROP = 0, 1
NO_IDS = np.zeros(0, dtype=np.int64)

__all__ = ["IDLE", "OFFERED", "PICKUP", "TRIP", "Dispatcher", "NearestDispatcher", "OrcaDispatcher", "hash2", "make_fleet"]


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
    agent: int = -1                  # the car holding this request, or -1
    declined: set = field(default_factory=set)
    offers: int = 0
    assigned_at: float = -1.0
    picked_at: float = -1.0
    log: list = field(default_factory=list)       # timeline: (t, kind, car)
    direct: float = -1.0             # estimated direct drive, pickup to drop-off (s); shared rides only
    max_ride: float = math.inf       # longest acceptable ride (s); shared rides only
    shared: bool = False             # rode together with another rider at some point


@dataclass(slots=True)
class Stop:
    kind: int                        # PICK, DROP or charging.CHARGE_STOP
    rs: RequestState | None
    x: float
    z: float
    deadline: float                  # latest acceptable arrival (shared rides); inf otherwise
    station: int = -1                # charging stop: the station


class _View:
    """What a city's router needs to know about one car (position, last node, remaining route)."""

    __slots__ = ("id", "node", "prev_node", "ri", "route", "x", "z")

    def __init__(self):
        self.ri = 0


class Fleet:
    """All cars as arrays, plus a shared arena holding every car's route waypoints."""

    def __init__(self, spec: list[tuple]):
        a = np.asarray(spec, dtype=float).reshape(-1, 5)
        n = self.n = len(a)
        self.x, self.z = a[:, 1].copy(), a[:, 2].copy()
        self.acc = a[:, 3].copy()
        self.next_ping = a[:, 4].copy()
        self.hx, self.hz = np.zeros(n), np.ones(n)              # unit heading vector
        self.node = np.full(n, -1, dtype=np.int64)               # street city: last intersection reached
        self.prev_node = np.full(n, -1, dtype=np.int64)
        self.state = np.zeros(n, dtype=np.int64)
        self.rebal = np.zeros(n, dtype=np.bool_)
        self.load = np.zeros(n, dtype=np.int64)                  # riders on board
        self.share_ok = np.zeros(n, dtype=np.bool_)              # every rider in the plan accepts sharing
        self.offline_until = np.full(n, -1.0)
        self.last_ping = np.zeros(n)
        self.rx, self.rz = self.x.copy(), self.z.copy()          # last reported GPS position
        self.cruise_wait = np.zeros(n)
        self.offers = np.zeros(n, dtype=np.int64)
        self.accepts = np.zeros(n, dtype=np.int64)
        self.offer_at, self.offer_deadline = np.zeros(n), np.zeros(n)
        self.offer_acc = np.zeros(n, dtype=np.bool_)
        self.odo = np.zeros((n, 4))                              # distance: cruising, repositioning, to pickup, loaded
        self.soc = np.ones(n)                                    # battery state of charge (electric fleet)
        self.tgt_x, self.tgt_z = np.full(n, -1.0), np.full(n, -1.0)
        self.off_until = np.full(n, -1.0)                        # off duty until (drivers who logged off)
        self.r_pos = np.zeros(n, dtype=np.int64)
        self.r_end = np.zeros(n, dtype=np.int64)
        self.arena = np.zeros((max(4096, 64 * n), 4))
        self.top = 0
        self.stops: list[list[Stop]] = [[] for _ in range(n)]
        self.offer_rs: list[RequestState | None] = [None] * n
        self.leg: list = [None] * n                              # telemetry for the current pickup leg

    def set_route(self, i: int, wp: np.ndarray):
        m = len(wp)
        if self.top + m > len(self.arena):
            self._compact(m)
        self.arena[self.top:self.top + m] = wp
        self.r_pos[i], self.r_end[i] = self.top, self.top + m
        self.top += m

    def clear_route(self, i: int):
        self.r_pos[i] = self.r_end[i] = 0

    def remaining(self, i: int) -> np.ndarray:
        return self.arena[self.r_pos[i]:self.r_end[i]]

    def _compact(self, extra: int):
        """Copy every live route to the front of a fresh arena (doubling it if needed)."""
        live = self.r_end - self.r_pos
        idx = np.flatnonzero(live > 0)
        lens = live[idx]
        total = int(lens.sum())
        cap = len(self.arena)
        while cap < 2 * (total + extra):
            cap *= 2
        offs = np.cumsum(lens) - lens
        src = np.repeat(self.r_pos[idx] - offs, lens) + np.arange(total)
        new = np.empty((cap, 4))
        new[:total] = self.arena[src]
        self.r_pos[:] = 0
        self.r_end[:] = 0
        self.r_pos[idx], self.r_end[idx] = offs, offs + lens
        self.arena, self.top = new, total


class Dispatcher:
    policy = "base"
    label = "Base"
    pool = False

    def __init__(self, world: World, fleet_spec: list[tuple], telemetry: list | None = None, ledger=None,
                 ev: str | None = None):
        self.world = world
        self.ledger = ledger
        self.city = G.current()
        n_zones = G.n_zones()
        self.z_sum = [0.0] * n_zones
        self.z_n = [0] * n_zones
        self.hist = [0] * 12                       # 30-second wait bands, last one is 5:30+
        self.timelines: dict[int, list] = {}
        self.tl_order: list[int] = []
        self.rng = random.Random(world.seed ^ 0xABC)
        self.np_rng = np.random.default_rng(world.seed ^ 0xABC)
        self.f = Fleet(fleet_spec)
        self.n = self.f.n
        self.queue: list[RequestState] = []
        self.active: list[RequestState] = []
        self.fs = FeatureStore()
        self.telemetry = telemetry
        self.last_outage = 0
        self.offline = 0
        self.batch_no = 0
        self.sample_timer = 0.0
        self.minute_timer = 0.0
        self.m = dict(served=0, lost=0, reoffers=0, timeouts=0, declines=0, waits=[], p1waits=[],
                      interval=[], interval_lost=0, series=[], all=[], dropped=0, shared=0, detour=[], ride=[])
        # spatial index over car positions
        self.B = 30.0
        self.BN = math.ceil(max(self.city.WX, self.city.WZ) / self.B) + 1
        self.cell_start = np.zeros(self.BN * self.BN + 1, dtype=np.int64)
        self.cell_items = np.zeros(self.n, dtype=np.int64)
        self.buf_d, self.buf_i = np.empty(self.n), np.empty(self.n, dtype=np.int64)
        self.out_arrive, self.out_cruise = np.empty(self.n, dtype=np.int64), np.empty(self.n, dtype=np.int64)
        self.knn_out = np.empty(max(C.K, C.POOL_K, 1), dtype=np.int64)
        self.lut = np.ascontiguousarray(self.city.zone_lut, dtype=np.int64)
        self.unit_km = getattr(self.city, "frame", {}).get("unit_m", 30.0) / 1000
        self._view = _View()
        self.ev = None                             # electric fleet: charging policy "naive" or "smart"
        self.rl_rec = None                         # orca.rl transition recorder (training runs only)
        self.bus = None                            # orca.events.EventBus, set by the engine when events are on
        if ev:
            from .charging import ChargingNetwork
            self.ev = ChargingNetwork(self, ev)

    # ---- main step ---------------------------------------------------------------
    def step(self, dt: float, new_reqs: list[Request]):
        w, t, f = self.world, self.world.t, self.f
        for o in w.outages:
            if o.id > self.last_outage:
                self.last_outage = o.id
                inside = (f.x - o.x) ** 2 + (f.z - o.z) ** 2 < o.r * o.r
                f.offline_until[inside] = np.maximum(f.offline_until[inside], o.until)
        u = self.np_rng.random((3, self.n))
        na, nc, self.offline = K.step_fleet(
            dt, t, self.city.base_speed, C.IDLE_CRUISE, C.PING_MIN, C.PING_JITTER, 0.00003,
            f.x, f.z, f.hx, f.hz, f.node, f.prev_node, f.state, f.rebal, f.load, f.offline_until, f.last_ping,
            f.next_ping, f.rx, f.rz, f.cruise_wait, f.r_pos, f.r_end, f.arena, f.odo, f.soc, self.energy_use(), u,
            self.lut, self.city.RASTER, w.zone_factor_array(), w.blob_array(), self.fs.speed, self.fs.speed_n,
            self.fs.city, self.out_arrive, self.out_cruise)
        for i in np.flatnonzero(f.state == OFFERED).tolist():
            rs = f.offer_rs[i]
            if rs is None or rs.status != "offered" or rs.agent != i:
                self.set_idle(i, f.cruise_wait[i])
            elif f.offline_until[i] < t and t >= f.offer_at[i]:
                self.offer_result(i, bool(f.offer_acc[i]))
            elif t >= f.offer_deadline[i]:
                self.m["timeouts"] += 1
                rs.log.append((t, "expire", i))
                self.release(i, rs)
        for i in self.out_arrive[:na].tolist():
            self.arrive(i)
        for i in self.out_cruise[:nc].tolist():
            self.cruise(i)
        if self.ev is not None:
            self.ev.step(dt)
        leaving = np.flatnonzero((f.off_until > t) & (f.state == IDLE))        # log off once their ride is done
        if len(leaving):
            f.state[leaving] = OFF
            f.r_pos[leaving] = f.r_end[leaving] = 0
        back = np.flatnonzero((f.state == OFF) & (f.off_until <= t))
        for i in back.tolist():
            self.set_idle(i, 1.0)

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

    def energy_use(self) -> float:
        """Battery used per world unit driven (0: not an electric fleet)."""
        return self.ev.use if self.ev is not None else 0.0

    def on_minute(self):
        pass

    def on_idle(self, i: int):
        """A car finished its plan and is free again."""

    def dispatch(self, dt):
        raise NotImplementedError

    # ---- movement ------------------------------------------------------------------
    def view(self, i: int) -> _View:
        f, v = self.f, self._view
        v.id, v.x, v.z, v.node, v.prev_node = i, float(f.x[i]), float(f.z[i]), int(f.node[i]), int(f.prev_node[i])
        v.route = f.remaining(i)
        return v

    def route_to(self, i: int, tx: float, tz: float):
        self.f.set_route(i, self.city.route(self.view(i), tx, tz))

    def cruise(self, i: int):
        self.f.set_route(i, self.city.cruise(self.view(i), self.rng))

    def set_idle(self, i: int, cruise_wait: float):
        f = self.f
        f.state[i], f.offer_rs[i], f.leg[i] = IDLE, None, None
        f.stops[i] = []
        f.share_ok[i] = False
        f.clear_route(i)
        f.cruise_wait[i] = cruise_wait
        f.tgt_x[i] = f.tgt_z[i] = -1.0

    def advance_plan(self, i: int):
        """Drive to the car's next stop, or go idle when the plan is done."""
        f = self.f
        stops = f.stops[i]
        if not stops:
            if self.ev is not None and f.soc[i] < C.EV_LOW:
                self.ev.go_charge(i)
            else:
                self.set_idle(i, 5 + self.rng.random() * 20)
                self.on_idle(i)
            return
        s = stops[0]
        f.state[i] = PICKUP if s.kind == PICK else TRIP
        f.rebal[i] = False
        f.share_ok[i] = all(st.rs is not None and st.rs.r.share for st in stops)
        f.tgt_x[i], f.tgt_z[i] = s.x, s.z
        self.route_to(i, s.x, s.z)

    def arrive(self, i: int):
        t, f = self.world.t, self.f
        stops = f.stops[i]
        if not stops:
            self.set_idle(i, 5 + self.rng.random() * 20)
            return
        s = stops.pop(0)
        rs = s.rs
        if rs is None:                               # reached the charging station
            self.ev.arrive(i)
            return
        if s.kind == PICK:
            rs.status, rs.picked_at = "ontrip", t
            f.load[i] += 1
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
            rs.log.append((t, "pickup", i))
            if f.load[i] >= 2:                       # riding together: everyone on board shares
                for st in stops:
                    if st.kind == DROP and st.rs.status == "ontrip":
                        st.rs.shared = True
            if self.ledger is not None:
                self.ledger.pickup(self.policy, rs.r, t, wait, i, rs.offers)
            if self.bus is not None:
                self.bus.emit("pickup", policy=self.policy, t=round(t, 1), req=rs.r.id, car=i, wait=round(wait, 1),
                              zone=rs.r.zone, riders=int(f.load[i]))
            leg = f.leg[i]
            if leg is not None and leg[2] is rs and self.telemetry is not None:
                self.telemetry.append((*leg[0], t - leg[1]))
            f.leg[i] = None
        else:
            rs.status = "done"
            f.load[i] -= 1
            self.m["dropped"] += 1
            if rs.shared:
                self.m["shared"] += 1
            self.m["ride"].append(t - rs.picked_at)
            if self.bus is not None:
                self.bus.emit("dropoff", policy=self.policy, t=round(t, 1), req=rs.r.id, car=i,
                              ride=round(t - rs.picked_at, 1), shared=int(rs.shared))
            if rs.direct > 0:
                self.m["detour"].append((t - rs.picked_at) / rs.direct)
        self.advance_plan(i)

    # ---- offers ----------------------------------------------------------------------
    def lose(self, rs: RequestState):
        i = rs.agent
        if i >= 0:
            f = self.f
            if f.state[i] == OFFERED and f.offer_rs[i] is rs:
                self.set_idle(i, 2.0)
            else:
                f.stops[i] = [s for s in f.stops[i] if s.rs is not rs]
                if f.leg[i] is not None and f.leg[i][2] is rs:
                    f.leg[i] = None
                if f.stops[i]:
                    self.advance_plan(i)
                else:
                    self.set_idle(i, 2.0)
        rs.status, rs.agent = "lost", -1
        rs.log.append((self.world.t, "cancel", None))
        if self.ledger is not None:
            self.ledger.lost(self.policy, rs.r, self.world.t)
        if self.bus is not None:
            self.bus.emit("cancel", policy=self.policy, t=round(self.world.t, 1), req=rs.r.id, zone=rs.r.zone)
        self.m["lost"] += 1
        self.m["interval_lost"] += 1

    def true_accept_p(self, i: int, rs: RequestState) -> float:
        f = self.f
        d = abs(rs.r.x - f.x[i]) + abs(rs.r.z - f.z[i])
        eta = d / (C.BASE_SPEED * 0.8) + 4
        return clamp(f.acc[i] - 0.03 * max(0.0, eta / 60 - 4) + (0.06 if rs.r.p1 else 0), 0.03, 0.995)

    def offer(self, i: int, rs: RequestState):
        t, f = self.world.t, self.f
        f.state[i], f.offer_rs[i], f.rebal[i] = OFFERED, rs, False
        f.clear_route(i)
        f.tgt_x[i], f.tgt_z[i] = rs.r.x, rs.r.z
        rs.status, rs.agent = "offered", i
        rs.log.append((t, "offer", i))
        rs.offers += 1
        f.offers[i] += 1
        f.offer_acc[i] = hash2(i * 131 + 7, rs.r.id * 7 + rs.offers) < self.true_accept_p(i, rs)
        f.offer_at[i] = t + 4 + hash2(i, rs.r.id) * 7
        f.offer_deadline[i] = t + C.OFFER_TIMEOUT

    def offer_result(self, i: int, accepted: bool):
        rs, t, f = self.f.offer_rs[i], self.world.t, self.f
        f.offer_rs[i] = None
        if accepted:
            f.accepts[i] += 1
            rs.status, rs.assigned_at = "assigned", t
            rs.log.append((t, "accept", i))
            if self.bus is not None:
                self.bus.emit("assign", policy=self.policy, t=round(t, 1), req=rs.r.id, car=i, shared=0)
            if self.telemetry is not None:
                f.leg[i] = (self.fs.eta_features(f.x[i], f.z[i], rs.r.x, rs.r.z, self.world.clock()), t, rs)
            r = rs.r
            pick_dl, drop_dl = math.inf, math.inf
            if self.pool and r.share:
                speed = self.world.speed_now()
                eta = float(self.city.ff_matrix([f.x[i]], [f.z[i]], [r.x], [r.z])[0, 0]) / speed
                self.plan_limits(rs, speed)
                pick_dl, drop_dl = r.t + C.ABANDON - 30, t + eta + rs.max_ride
            f.stops[i] = [Stop(PICK, rs, r.x, r.z, pick_dl), Stop(DROP, rs, r.dx, r.dz, drop_dl)]
            self.advance_plan(i)
        else:
            self.m["declines"] += 1
            rs.log.append((t, "decline", i))
            self.release(i, rs)

    def plan_limits(self, rs: RequestState, speed: float):
        """Direct drive time and the longest acceptable shared ride for a rider."""
        if rs.direct < 0:
            r = rs.r
            rs.direct = max(1.0, float(self.city.ff_matrix([r.x], [r.z], [r.dx], [r.dz])[0, 0]) / speed)
            rs.max_ride = rs.direct * (1 + C.POOL_DETOUR) + C.POOL_SLACK

    def release(self, i: int, rs: RequestState):
        self.m["reoffers"] += 1
        rs.declined.add(i)
        rs.status, rs.agent = "waiting", -1
        self.queue.append(rs)
        self.set_idle(i, 3.0)

    # ---- spatial index ------------------------------------------------------------------
    def build_index(self, fresh_only: bool):
        f = self.f
        mask = f.state == IDLE
        if fresh_only:
            mask &= self.world.t - f.last_ping <= C.STALE_AFTER
        K.build_grid(f.rx, f.rz, mask, self.B, self.BN, self.cell_start, self.cell_items)

    def nearest_idle(self, x: float, z: float, k: int, excluded: set) -> np.ndarray:
        f = self.f
        excl = np.fromiter(excluded, dtype=np.int64, count=len(excluded)) if excluded else NO_IDS
        cnt = K.knn(x, z, k, excl, f.rx, f.rz, f.state, IDLE, self.B, self.BN, self.cell_start, self.cell_items,
                    self.buf_d, self.buf_i, self.knn_out)
        return self.knn_out[:cnt].copy()

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
        t, f = self.world.t, self.f
        pct = lambda q: s[min(len(s) - 1, int(q * len(s)))] if s else 0.0
        p1 = self.m["p1waits"]
        waiting = sum(1 for rs in self.active if rs.status in ("waiting", "offered"))
        km = float(f.odo.sum()) * self.unit_km
        dropped, det = self.m["dropped"], self.m["detour"]
        return dict(mean=sum(s) / len(s) if s else 0.0, p50=pct(0.5), p95=pct(0.95),
                    p1=sum(p1) / len(p1) if p1 else 0.0, served=self.m["served"], lost=self.m["lost"],
                    reoffers=self.m["reoffers"], timeouts=self.m["timeouts"],
                    util=float(np.count_nonzero((f.state != IDLE) & (f.state != OFF))) / max(1, self.n - int(np.count_nonzero(f.state == OFF))),
                    off_duty=int(np.count_nonzero(f.state == OFF)),
                    offline=int(np.count_nonzero(f.offline_until >= t)), waiting=waiting,
                    veh_km=km, km_per_rider=km / dropped if dropped else 0.0,
                    svc_km_per_rider=float(f.odo[:, K.ODO_PICKUP:].sum()) * self.unit_km / dropped if dropped else 0.0,
                    occupancy=float(f.odo[:, K.ODO_LOADED].sum() / max(1e-9, f.odo.sum())),
                    shared=self.m["shared"] / dropped if dropped else 0.0,
                    detour=float(np.mean(det)) if det else 0.0,
                    ride=float(np.mean(self.m["ride"])) if self.m["ride"] else 0.0,
                    **(self.ev.stats() if self.ev is not None else {}))


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
            r = rs.r
            best = self.nearest_idle(r.x, r.z, 1 if self.ev is None else 6, rs.declined)
            if self.ev is not None and len(best):           # the nearest car with charge enough for the ride
                best = best[self.ev.can_serve(best, r.x, r.z, r.dx, r.dz)]
            if len(best):
                self.offer(int(best[0]), rs)
            else:
                self.queue.append(rs)


def deadline_cost(wait, eta):
    """Penalty for a pairing that would reach the rider close to or after the moment they give up.

    Riders cancel after C.ABANDON seconds. A logistic ramp over the last two minutes before that point
    makes ORCA prefer, for riders who have waited long, a car that arrives in time over a cheaper one
    that would arrive too late; without it a 16-minute arrival scores almost like a 14-minute one.
    Works on scalars and arrays.
    """
    if C.DEADLINE_W <= 0:
        return 0.0 * np.asarray(wait)
    slack = C.ABANDON - (np.asarray(wait) + np.asarray(eta) + 30.0)    # 30 s margin for the offer round-trip
    return C.DEADLINE_W * C.DEADLINE_COST * expit(-slack / 40.0)


class OrcaDispatcher(Dispatcher):
    policy = "smart"
    label = "ORCA"

    def __init__(self, world, fleet_spec, telemetry=None, use_learned=True, ledger=None, pool: bool = False,
                 ev: str | None = None, rebalance: str = "rules", rl_net: str | None = None, rl_explore: float = 0.0,
                 rl_log: bool = False):
        super().__init__(world, fleet_spec, telemetry, ledger, ev=ev)
        self.pool = pool
        # repositioning of idle cars: "rules" (forecast deficits), "learned" (orca.rl value network) or "off"
        self.rebalance_mode, self.learned, self.rl_rec = rebalance, None, None
        if rebalance == "learned":
            from pathlib import Path

            from . import rl
            net = rl.ValueNet.load(Path(rl_net) if rl_net else rl.net_path(self.city.name))
            if net is None:
                raise FileNotFoundError(f"no learned repositioning model for {self.city.name}: python -m orca.rl train")
            self.learned = rl.LearnedRebalancer(self, net, rl_explore, seed=world.seed)
        if rl_log:
            from . import rl
            self.rl_rec = rl.Recorder(self)
        if pool:
            self.label = "ORCA shared"
            self.pcell_start = np.zeros(self.BN * self.BN + 1, dtype=np.int64)
            self.pcell_items = np.zeros(self.n, dtype=np.int64)
        self.eta = EtaModel(self.fs, use_learned)
        self.accept = AcceptanceModel()
        self.forecaster = DemandForecaster(self.fs, use_learned, "tlc" if world.source.name == "nyc-tlc" else "sim")
        self.z_idle = np.zeros(G.n_zones(), dtype=np.int64)
        self.deficit_zones: list[int] = []
        self.batch_timer = 0.0
        self.rebal_timer = 0.0
        self.last_rebal_log = -1e9
        self.explain: dict[int, dict] = {}
        self.explain_order: list[int] = []
        self.notable: dict | None = None
        self.solver_used = "hungarian"
        self.solve_ms = 0.0
        self.decisions = 0                     # requests matched, for decisions per second
        self.batch_log: list[tuple[int, float]] = []   # (requests in the batch, ms to decide them), last 5,000
        from .engine import Histogram
        self.match_hist = Histogram()
        # shared-ride funnel, counted per sharing request and batch: asked, a busy car near, a feasible plan, chosen
        self.pool_funnel = dict(asked=0, near=0, feasible=0, chosen=0)

    def on_minute(self):
        self.forecaster.update(self.world.clock())

    def on_idle(self, i: int):
        if self.rl_rec is not None:
            self.rl_rec.on_idle(i)

    def dispatch(self, dt):
        self.batch_timer += dt
        if self.batch_timer >= C.BATCH:
            self.batch_timer -= C.BATCH
            self.match()
        self.rebal_timer += dt
        if self.rebal_timer >= C.REBAL:
            self.rebal_timer -= C.REBAL
            self.rebalance()

    def idle_fresh(self) -> np.ndarray:
        f = self.f
        return (f.state == IDLE) & (self.world.t - f.last_ping <= C.STALE_AFTER)

    def compute_zone_idle(self):
        f = self.f
        m = self.idle_fresh()
        self.z_idle = np.bincount(self.city.zone_of_many(f.rx[m], f.rz[m]), minlength=G.n_zones())

    def zone_cost(self, x, z):
        """Cost (s) of taking an idle car out of a zone forecast to run short (scalars or arrays)."""
        zi = self.city.zone_of_many(np.atleast_1d(x), np.atleast_1d(z))
        ratio = np.asarray(self.forecaster.forecast, dtype=float)[zi] * C.COVER / (self.z_idle[zi] + 1)
        out = np.where(ratio > 1, np.minimum(C.ZONE_CAP, (ratio - 1) * C.ZONE_SLOPE), 0.0)
        return out if np.ndim(x) else float(out[0])

    # ---- matching -----------------------------------------------------------------------
    def match(self):
        t, clock, f = self.world.t, self.world.clock(), self.f
        self.batch_no += 1
        q = [rs for rs in self.queue if rs.status == "waiting"]
        self.queue = []
        if not q:
            return
        self.build_index(fresh_only=True)
        self.compute_zone_idle()
        t0 = time.perf_counter()
        nq = len(q)
        qx = np.array([rs.r.x for rs in q])
        qz = np.array([rs.r.z for rs in q])
        qt = np.array([rs.r.t for rs in q])
        qp1 = np.array([rs.r.p1 for rs in q])
        excl = [np.fromiter(rs.declined, dtype=np.int64, count=len(rs.declined)) for rs in q]
        excl_off = np.cumsum([0] + [len(e) for e in excl]).astype(np.int64)
        excl_flat = np.concatenate(excl) if excl_off[-1] else NO_IDS
        out = np.full((nq, C.K), -1, dtype=np.int64)
        counts = np.zeros(nq, dtype=np.int64)
        K.knn_batch(qx, qz, C.K, excl_flat, excl_off, f.rx, f.rz, f.state, IDLE, self.B, self.BN,
                    self.cell_start, self.cell_items, self.buf_d, self.buf_i, out, counts)
        qi = np.repeat(np.arange(nq), counts)
        ci = out[out >= 0]
        ax, az = f.rx[ci], f.rz[ci]
        eta = self.eta.predict(ax, az, qx[qi], qz[qi], clock)
        p = self.accept.p_many(f.accepts[ci], f.offers[ci], eta, qp1[qi])
        zc = self.zone_cost(ax, az)
        cand = dict(qi=qi, ci=ci, eta=eta, p=p, zc=zc, cost=eta + (1 - p) * C.REOFFER + zc,
                    kind=np.zeros(len(ci), dtype=np.int64), pi=np.full(len(ci), -1), pj=np.full(len(ci), -1))
        if self.pool:
            extra = self.pool_candidates(q, t)
            if extra is not None:
                for k in cand:
                    cand[k] = np.concatenate([cand[k], extra[k]])
        if self.ev is not None:                      # only cars with charge enough for the ride
            qd = np.array([rs.r.dx for rs in q]), np.array([rs.r.dz for rs in q])
            c, qq = cand["ci"], cand["qi"]
            ok = np.where(cand["kind"] == 0, self.ev.can_serve(c, qx[qq], qz[qq], qd[0][qq], qd[1][qq]),
                          f.soc[c] >= C.EV_LOW + 0.15)
            cand = {k: v[ok] for k, v in cand.items()}
        qi, eta = cand["qi"], cand["eta"]
        wait = t - qt[qi]
        cand["adj"] = (cand["cost"] * np.where(qp1[qi], C.URGENT_W, 1.0) - np.minimum(wait, C.WAIT_CAP) * C.WAIT_W
                       + deadline_cost(wait, eta))
        chosen = self.solve(qi, cand["ci"], cand["adj"], nq)
        self.solve_ms = (time.perf_counter() - t0) * 1000
        self.decisions += len(chosen)
        self.batch_log.append((nq, self.solve_ms))
        self.match_hist.observe(self.solve_ms)
        if len(self.batch_log) > 5000:
            del self.batch_log[:1000]
        for k in chosen:
            rs, i = q[qi[k]], int(cand["ci"][k])
            if cand["kind"][k] == 0:
                self.offer(i, rs)
            else:
                self.insert(i, rs, int(cand["pi"][k]), int(cand["pj"][k]), float(eta[k]))
                self.pool_funnel["chosen"] += 1
        self.record_explain([q[qi[k]] for k in chosen], chosen, cand)
        for rs in q:
            if rs.status == "waiting":
                self.queue.append(rs)

    def solve(self, qi: np.ndarray, ci: np.ndarray, adj: np.ndarray, nq: int) -> list[int]:
        """Exact min-cost assignment of requests to cars for this batch; returns the chosen candidate rows.

        Every real pair costs less than leaving a request unassigned, so the solution serves as many requests
        as possible and, among those, at least total cost. Small batches use the dense Hungarian method; large
        ones (a 10,000-car fleet under a demand spike) the sparse LAPJV method on only the candidate pairs, with
        one costly "wait for the next batch" column per request so that a complete matching always exists.
        """
        if len(ci) == 0:
            return []
        cars, cj = np.unique(ci, return_inverse=True)
        m = len(cars)
        if nq <= C.HUNGARIAN_MAX:
            self.solver_used = "hungarian"
            BIG = 1e6
            M = np.full((nq, m), BIG)
            M[qi, cj] = adj + 400          # shift so every real pair is positive and preferred over none
            row_k = np.full((nq, m), -1, dtype=np.int64)
            row_k[qi, cj] = np.arange(len(ci))
            rows, cols = linear_sum_assignment(M)
            return [int(row_k[r, c]) for r, c in zip(rows, cols) if M[r, c] < BIG]
        self.solver_used = "sparse LAPJV"
        w = np.r_[adj + 400, np.full(nq, 1e6)]
        A = csr_matrix((w, (np.r_[qi, np.arange(nq)], np.r_[cj, m + np.arange(nq)])), shape=(nq, m + nq))
        rows, cols = min_weight_full_bipartite_matching(A)
        pair_k = {(int(q), int(c)): k for k, (q, c) in enumerate(zip(qi.tolist(), cj.tolist()))}
        return [pair_k[(int(r), int(c))] for r, c in zip(rows, cols) if c < m]

    # ---- shared rides -------------------------------------------------------------------
    def pool_candidates(self, q: list[RequestState], t: float) -> dict | None:
        """Busy cars that could take each sharing rider on board, at their cheapest feasible insertion."""
        f = self.f
        share = [k for k, rs in enumerate(q) if rs.r.share]
        if not share:
            return None
        poolable = (((f.state == PICKUP) | (f.state == TRIP)) & f.share_ok
                    & (t - f.last_ping <= C.STALE_AFTER)).astype(np.int64)
        if not poolable.any():
            self.pool_funnel["asked"] += len(share)
            return None
        K.build_grid(f.rx, f.rz, poolable.astype(np.bool_), self.B, self.BN, self.pcell_start, self.pcell_items)
        speed = self.world.speed_now()
        rows = []
        fun = self.pool_funnel
        for k in share:
            rs = q[k]
            r = rs.r
            fun["asked"] += 1
            cnt = K.knn(r.x, r.z, C.POOL_K, NO_IDS, f.rx, f.rz, poolable, 1, self.B, self.BN, self.pcell_start,
                        self.pcell_items, self.buf_d, self.buf_i, self.knn_out)
            if cnt == 0:
                continue
            fun["near"] += 1
            before = len(rows)
            self.plan_limits(rs, speed)
            pick_dl = r.t + C.ABANDON - 30
            for i in self.knn_out[:cnt].tolist():
                stops = f.stops[i]
                px = [f.rx[i], *(s.x for s in stops), r.x, r.dx]
                pz = [f.rz[i], *(s.z for s in stops), r.z, r.dz]
                T = self.city.ff_matrix(px, pz, px, pz) / speed
                delta = np.array([1 if s.kind == PICK else -1 for s in stops], dtype=np.int64)
                dl = np.array([s.deadline for s in stops], dtype=float)
                cost, pi, pj, eta = K.best_insertion(T, t, 0.0, delta, dl, int(f.load[i]), C.POOL_CAP, pick_dl,
                                                     rs.max_ride, rs.direct, C.POOL_DELAY_W)
                if np.isfinite(cost):
                    rows.append((k, i, eta, cost, pi, pj))
            fun["feasible"] += len(rows) > before
        if not rows:
            return None
        a = np.array(rows, dtype=float)
        n = len(a)
        return dict(qi=a[:, 0].astype(np.int64), ci=a[:, 1].astype(np.int64), eta=a[:, 2], p=np.ones(n),
                    zc=np.zeros(n), cost=a[:, 3], kind=np.ones(n, dtype=np.int64), pi=a[:, 4].astype(np.int64),
                    pj=a[:, 5].astype(np.int64))

    def insert(self, i: int, rs: RequestState, pi: int, pj: int, eta: float):
        """Add a sharing rider to car i's plan: pickup after its first pi stops, drop-off after pj."""
        t, f, r = self.world.t, self.f, rs.r
        stops = f.stops[i]
        pick = Stop(PICK, rs, r.x, r.z, r.t + C.ABANDON - 30)
        drop = Stop(DROP, rs, r.dx, r.dz, t + eta + rs.max_ride)
        f.stops[i] = [*stops[:pi], pick, *stops[pi:pj], drop, *stops[pj:]]
        rs.status, rs.agent, rs.assigned_at = "assigned", i, t
        rs.offers += 1
        rs.log.append((t, "offer", i))
        rs.log.append((t, "accept", i))          # drivers on shared rides take added riders automatically
        if self.bus is not None:
            self.bus.emit("assign", policy=self.policy, t=round(t, 1), req=r.id, car=i, shared=1)
        self.advance_plan(i)

    # ---- explanations ---------------------------------------------------------------------
    def record_explain(self, reqs: list[RequestState], ks: list[int], cand: dict):
        """Why each request in this batch got its car, against the car the nearest-car rule would pick."""
        if not reqs:
            return
        f = self.f
        # the nearest-car rule's choice for every request: nearest idle car in a straight line (or ORCA's own)
        base_ids = []
        for rs, k in zip(reqs, ks):
            ok = f.state == IDLE
            ok[int(cand["ci"][k])] = True
            if rs.declined:
                ok[list(rs.declined)] = False
            idx = np.flatnonzero(ok)
            base_ids.append(int(idx[np.argmin((f.rx[idx] - rs.r.x) ** 2 + (f.rz[idx] - rs.r.z) ** 2)]) if len(idx) else -1)
        have = [j for j, b in enumerate(base_ids) if b >= 0]
        etas = np.zeros(len(reqs))
        if have:
            b = np.array([base_ids[j] for j in have])
            etas[have] = self.eta.predict(f.rx[b], f.rz[b], np.array([reqs[j].r.x for j in have]),
                                          np.array([reqs[j].r.z for j in have]), self.world.clock())
        for rs, k, best, eta in zip(reqs, ks, base_ids, etas.tolist()):
            self._explain_one(rs, k, cand, best, eta)

    def _explain_one(self, rs: RequestState, k: int, cand: dict, best: int, eta: float):
        t, r, f = self.world.t, rs.r, self.f

        def row(j):
            i = int(cand["ci"][j])
            return dict(id=i, eta=float(cand["eta"][j]), p=float(cand["p"][j]), zc=float(cand["zc"][j]),
                        cost=float(cand["cost"][j]), age=float(t - f.last_ping[i]), rx=float(f.rx[i]), rz=float(f.rz[i]),
                        pool=bool(cand["kind"][j]), riders=int(f.load[i]))
        mine = np.flatnonzero(cand["qi"] == cand["qi"][k])
        top = mine[np.argsort(cand["cost"][mine], kind="stable")][:5].tolist()
        if k not in top:
            top[-1] = k
        chosen = row(k)
        cands = []
        for j in top:
            c = row(j)
            c["chosen"] = j == k
            c["taken"] = bool(j != k and not c["pool"] and f.state[c["id"]] != IDLE)
            cands.append(c)
        base = None
        if best >= 0:
            p = float(self.accept.p_many(f.accepts[best], f.offers[best], eta, r.p1))
            zc = self.zone_cost(float(f.rx[best]), float(f.rz[best]))
            age = float(t - f.last_ping[best])
            same = best == chosen["id"]
            if same:
                reason = "Same unit. Both policies agree here."
            elif chosen["pool"]:
                reason = (f"ORCA added this rider to car {chosen['id']}, which already has a rider going the same way, "
                          "and kept the nearest idle car free for someone else.")
            elif age > C.STALE_AFTER:
                reason = f"Its GPS was last seen {round(age)}s ago, so it is probably unreachable and the offer would hang until it times out."
            elif p < chosen["p"] - 0.12:
                reason = f"Its driver accepts only about {round(p * 100)}% of offers. A decline costs about {int(C.REOFFER)}s of re-offering."
            elif eta > chosen["eta"] + 8:
                reason = f"It is closer in a straight line, but {round(eta - chosen['eta'])}s slower by road and traffic."
            elif zc > 5:
                reason = f"It is one of the last idle cars in {zone_name(self.city.zone_of(f.rx[best], f.rz[best]))}, where demand is about to rise."
            else:
                reason = "It is the nearest unit, but the batch is cheaper overall with it serving another request."
            base = dict(id=best, eta=eta, p=p, zc=zc, cost=eta + (1 - p) * C.REOFFER + zc, age=age,
                        rx=float(f.rx[best]), rz=float(f.rz[best]), reason=reason, same=same)
        rec = dict(id=r.id, clock=self.world.clock(), p1=r.p1, zone=r.zone, zone_name=zone_name(r.zone), batch=self.batch_no,
                   x=r.x, z=r.z, wait=t - r.t, attempt=rs.offers, solver=self.solver_used, chosen=chosen, cands=cands, base=base)
        self.explain[r.id] = rec
        self.explain_order.append(r.id)
        if len(self.explain_order) > 800:
            self.explain.pop(self.explain_order.pop(0), None)
        if base and not base["same"] and (r.p1 or base["age"] > C.STALE_AFTER or self.rng.random() < 0.25):
            self.notable = rec

    # ---- rebalancing ----------------------------------------------------------------------
    def rebalance(self):
        N, f = G.n_zones(), self.f
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
        if self.ev is not None and self.ev.policy == "smart":
            self.ev.top_up(self.z_idle, np.asarray(fc, dtype=float) * C.COVER)
        if self.rebalance_mode == "off":
            return
        free = np.flatnonzero(self.idle_fresh() & ~f.rebal)
        zones = self.city.zone_of_many(f.rx[free], f.rz[free])
        if self.learned is not None:
            moved = self.learned.step(free, zones, np.asarray(fc, dtype=float) * C.COVER)
            t = self.world.t
            if moved >= 12 and t - self.last_rebal_log > 420:
                self.last_rebal_log = t
                self.world.log("rebal", f"ORCA's learned policy moved {moved} idle cars toward busier zones")
            return
        if not deficit:
            return
        by_zone: list[list[int]] = [[] for _ in range(N)]
        for i, z in zip(free.tolist(), zones.tolist()):
            by_zone[z].append(i)
        budget, moved = math.ceil(self.n * 0.015), 0
        for zi, d in deficit:
            want = min(math.ceil(d), budget)
            for sz in sorted((k for k, v in surplus.items() if v > 0 and zone_dist(k, zi) < 2.9), key=lambda k: zone_dist(k, zi)):
                pool = by_zone[sz]
                while want > 0 and surplus[sz] > 0 and pool:
                    i = pool.pop()
                    self.route_to(i, *self.city.point_in_zone(zi, self.rng))
                    f.rebal[i], f.cruise_wait[i] = True, 0.0
                    want -= 1
                    budget -= 1
                    moved += 1
                    surplus[sz] -= 1
                if want <= 0:
                    break
            if budget <= 0:
                break
        t = self.world.t
        if moved >= 12 and t - self.last_rebal_log > 420:
            self.last_rebal_log = t
            self.world.log("rebal", f"ORCA moved {moved} idle cars toward "
                                    + ", ".join(zone_name(d[0]) for d in deficit[:3]) + " ahead of forecast demand")
