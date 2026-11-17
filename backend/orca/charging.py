"""Electric fleet: batteries, charging stations with a limited number of plugs, and charging policies.

Batteries drain with every metre driven (kernels.step_fleet). A car below ``EV_LOW`` must charge;
a car is only offered a ride it can finish with ``EV_RESERVE`` left. At a station a car takes a
free plug or joins the queue, charges at the station's power to ``EV_TARGET`` and returns to service.

Two policies, so the benchmark can separate dispatch from charging:

* ``naive`` (what drivers do on their own): at ``EV_LOW``, drive to the nearest DC fast station.
* ``smart`` (ORCA): choose the station that returns the car to service soonest: driving there,
  the expected queue (cars plugged, queued and on their way, over its plugs) and the charge itself
  at that station's power. When the whole city has more idle cars than the demand forecast needs
  (beyond a margin of ``EV_TOPUP_MARGIN`` of the fleet), a quarter of that surplus, the emptiest
  idle cars below ``EV_TOPUP`` in zones with cars to spare, top up at a nearby free fast plug, so
  fewer cars have to stop for charge in the next peak.

Stations are real (orca.chargers, from OpenStreetMap); the synthetic grid gets eight evenly spread ones.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from . import chargers
from . import config as C

CHARGE_STOP = 2


class ChargingNetwork:
    def __init__(self, d, policy: str):
        self.d, self.policy = d, policy
        city = d.city
        st = chargers.load(city) if hasattr(city, "frame") else None
        if st is None or not len(st["x"]):
            xs = np.array([0.2, 0.5, 0.8, 0.2, 0.8, 0.2, 0.5, 0.8])
            zs = np.array([0.2, 0.2, 0.2, 0.5, 0.5, 0.8, 0.8, 0.8])
            pts = [city.snap(x * city.WX, z * city.WZ) for x, z in zip(xs, zs)]
            st = dict(x=np.array([p[0] for p in pts]), z=np.array([p[1] for p in pts]), plugs=np.full(8, 4),
                      kw=np.full(8, 100.0), dc=np.ones(8, dtype=bool), name=[f"Station {k + 1}" for k in range(8)])
        self.x, self.z, self.plugs, self.kw, self.dc = st["x"], st["z"], st["plugs"].astype(np.int64), st["kw"], st["dc"]
        self.names = st["name"]
        self.ns = len(self.x)
        self.used = np.zeros(self.ns, dtype=np.int64)
        self.coming = np.zeros(self.ns, dtype=np.int64)
        self.queue = [deque() for _ in range(self.ns)]
        n = d.n
        f = d.f
        rng = np.random.default_rng(d.world.seed * 31 + 7)            # the same batteries for both policies
        f.soc[:] = rng.uniform(*C.EV_SOC0, n)
        self.station = np.full(n, -1, dtype=np.int64)
        self.plugged = np.zeros(n, dtype=np.bool_)
        self.since = np.zeros(n)
        self.use = C.EV_KWH_PER_KM * d.unit_km / C.EV_BATTERY_KWH      # state of charge per world unit
        self.m = dict(sessions=0, queue_wait=[], kwh=0.0, stranded=0, topups=0, charge_s=0.0)
        self.empty = np.zeros(n, dtype=np.bool_)

    # ---- planning ---------------------------------------------------------------------------
    def can_serve(self, cars, px, pz, dx, dz) -> np.ndarray:
        """Cars with enough charge to reach the pickup, finish the ride and keep the reserve."""
        f = self.d.f
        km = (np.hypot(f.x[cars] - px, f.z[cars] - pz) + np.hypot(np.subtract(dx, px), np.subtract(dz, pz))) * C.EV_DETOUR
        return f.soc[cars] - km * self.use >= C.EV_RESERVE

    def choose(self, i: int) -> int:
        f = self.d.f
        dist = np.hypot(self.x - f.x[i], self.z - f.z[i]) * C.EV_DETOUR
        if self.policy == "naive":
            pool = np.flatnonzero(self.dc) if self.dc.any() else np.arange(self.ns)
            return int(pool[np.argmin(dist[pool])])
        speed = self.d.world.speed_now() * 0.8                         # typical door-to-door speed, units / s
        drive = dist / speed
        charge = max(0.0, C.EV_TARGET - f.soc[i] + dist.min() * self.use) * C.EV_BATTERY_KWH / self.kw * 3600
        session = (C.EV_TARGET - C.EV_LOW) * C.EV_BATTERY_KWH / self.kw * 3600
        ahead = self.used + np.array([len(q) for q in self.queue]) + self.coming - self.plugs + 1
        wait = np.maximum(0, ahead) * session / self.plugs
        reach = f.soc[i] - dist * self.use > 0.02
        total = np.where(reach, drive + np.maximum(wait - drive, 0) + charge, np.inf)
        return int(np.argmin(total))

    def go_charge(self, i: int, j: int | None = None):
        from .dispatch import CHARGE, Stop
        d, f = self.d, self.d.f
        j = self.choose(i) if j is None else j
        f.stops[i] = [Stop(CHARGE_STOP, None, float(self.x[j]), float(self.z[j]), math.inf, j)]
        f.state[i], f.rebal[i], f.share_ok[i] = CHARGE, False, False
        f.tgt_x[i], f.tgt_z[i] = self.x[j], self.z[j]
        self.station[i] = j
        self.coming[j] += 1
        d.route_to(i, float(self.x[j]), float(self.z[j]))

    def arrive(self, i: int):
        from .dispatch import PLUGGED
        f, t = self.d.f, self.d.world.t
        j = int(self.station[i])
        self.coming[j] -= 1
        f.stops[i] = []
        f.state[i] = PLUGGED
        f.clear_route(i)
        self.since[i] = t
        if self.used[j] < self.plugs[j]:
            self._plug(i, j)
        else:
            self.queue[j].append(i)

    def _plug(self, i: int, j: int):
        t = self.d.world.t
        self.used[j] += 1
        self.plugged[i] = True
        self.m["queue_wait"].append(t - self.since[i])
        self.m["sessions"] += 1

    # ---- every tick ---------------------------------------------------------------------------
    def step(self, dt: float):
        from .dispatch import CHARGE, IDLE, PLUGGED
        d, f = self.d, self.d.f
        self.m["charge_s"] += float(np.count_nonzero((f.state == CHARGE) | (f.state == PLUGGED))) * dt
        on = np.flatnonzero(self.plugged)
        if len(on):
            add = self.kw[self.station[on]] * dt / 3600 / C.EV_BATTERY_KWH
            f.soc[on] += add
            self.m["kwh"] += float(add.sum()) * C.EV_BATTERY_KWH
            for i in on[f.soc[on] >= C.EV_TARGET].tolist():
                j = int(self.station[i])
                self.plugged[i] = False
                self.station[i] = -1
                self.used[j] -= 1
                d.set_idle(i, 2.0)
                if self.queue[j]:
                    self._plug(self.queue[j].popleft(), j)
        newly = (f.soc <= 0) & ~self.empty
        if newly.any():
            self.m["stranded"] += int(newly.sum())
            self.empty |= newly
        low = np.flatnonzero((f.state == IDLE) & (f.soc < C.EV_LOW))
        for i in low.tolist():
            self.go_charge(i)

    def top_up(self, z_idle: np.ndarray, need: np.ndarray):
        """ORCA: while the city has cars to spare, the emptiest idle cars top up at a nearby free fast plug."""
        from .dispatch import IDLE
        d, f = self.d, self.d.f
        budget = int((z_idle.sum() - need.sum() - C.EV_TOPUP_MARGIN * d.n) * 0.25)
        free = self.plugs - self.used - self.coming - np.array([len(q) for q in self.queue])
        free[self.kw < 40] = 0                       # topping up on a slow plug would keep a car away for hours
        if budget <= 0 or (free <= 0).all():
            return
        cand = np.flatnonzero((f.state == IDLE) & (f.soc < C.EV_TOPUP) & (d.world.t - f.last_ping <= C.STALE_AFTER))
        if not len(cand):
            return
        zones = d.city.zone_of_many(f.rx[cand], f.rz[cand])
        spare = z_idle - need
        cand = cand[spare[zones] >= 2]
        for i in cand[np.argsort(f.soc[cand])][:budget].tolist():
            dist = np.hypot(self.x - f.x[i], self.z - f.z[i])
            ok = np.flatnonzero((free > 0) & (dist < C.EV_TOPUP_RADIUS))
            if not len(ok):
                continue
            j = int(ok[np.argmin(dist[ok] / np.maximum(self.kw[ok], 1) ** 0.5)])
            free[j] -= 1
            self.go_charge(i, j)
            self.m["topups"] += 1

    # ---- reporting ------------------------------------------------------------------------------
    def stats(self) -> dict:
        from .dispatch import CHARGE, PLUGGED
        f = self.d.f
        qw = self.m["queue_wait"]
        return dict(soc_mean=float(f.soc.mean()), soc_low=float(np.mean(f.soc < C.EV_LOW)),
                    to_charger=int(np.count_nonzero(f.state == CHARGE)), charging=int(self.plugged.sum()),
                    queued=int(np.count_nonzero((f.state == PLUGGED) & ~self.plugged)),
                    sessions=self.m["sessions"], queue_wait=float(np.mean(qw)) if qw else 0.0, kwh=self.m["kwh"],
                    stranded=self.m["stranded"], topups=self.m["topups"], charge_hours=self.m["charge_s"] / 3600)

    def snapshot(self) -> list:
        """Stations for the map: x, z, plugs, plugs in use, cars queued, DC fast."""
        return [[round(float(x), 1), round(float(z), 1), int(p), int(u), len(q), bool(dc)]
                for x, z, p, u, q, dc in zip(self.x, self.z, self.plugs, self.used, self.queue, self.dc)]

