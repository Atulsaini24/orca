"""Shared world state: clock, traffic, demand and injected incidents.

Both dispatch policies read the same World, so they face the identical
request stream, traffic and outages. Any difference in outcome comes from
the dispatch policy alone.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from . import city as G
from . import config as C
from .city import clamp, zone_name, zone_of

TRAFFIC_PROFILE = Path(__file__).resolve().parents[1] / "artifacts" / "nyc_traffic_profile.json"


def load_traffic() -> list[float] | None:
    """Real Manhattan taxi speed by hour, relative to the 08:00-20:00 average (scripts/prepare_tlc.py)."""
    try:
        return [min(1.6, max(0.7, f)) for f in json.loads(TRAFFIC_PROFILE.read_text())["factor"]]
    except (OSError, ValueError, KeyError):
        return None


@dataclass(slots=True)
class Request:
    id: int
    t: float
    x: float
    z: float
    dx: float
    dz: float
    p1: bool
    zone: int


@dataclass(slots=True)
class Blob:
    """A moving patch of slow traffic (or an accident when `accident` is set)."""
    x: float
    z: float
    vx: float
    vz: float
    r: float
    s: float
    until: float = math.inf
    accident: bool = False


@dataclass(slots=True)
class Hotspot:
    x: float
    z: float
    vx: float
    vz: float
    s: float
    w: float
    until: float = math.inf
    spike: bool = False
    boost: float = 0.0


@dataclass(slots=True)
class Outage:
    id: int
    x: float
    z: float
    r: float
    until: float


@dataclass(slots=True)
class Event:
    seq: int
    t: float
    clock: float
    type: str
    msg: str


class SyntheticDemand:
    """Poisson arrivals from drifting hotspots plus a uniform background."""

    name = "synthetic"

    def __init__(self, world: World):
        self.world = world
        r = world.rng
        city = world.city
        self.hotspots = [
            Hotspot(*world.inner_point(0.15), (r.random() - 0.5) * 0.04, (r.random() - 0.5) * 0.04, 14 + r.random() * 16, 0.6 + r.random())
            for _ in range(5)
        ]
        self.hotspots.append(Hotspot(*city.snap(city.WX * 0.5, city.WZ * 0.48), 0, 0, 30, 1.6))

    def base_rate(self) -> float:
        w = self.world
        return w.fleet * C.DEMAND_PER_UNIT * w.city.demand_scale * w.demand_mult * w.rush_factor()

    def step(self, dt: float) -> list[tuple[float, float, float, float]]:
        out = []
        for _ in range(self.world.poisson(self.base_rate() * dt)):
            x, z = self.world.sample_from(self.hotspots, 0.68)
            out.append((x, z, *self.world.random_dropoff(x, z)))
        return out

    def drift(self, dt: float):
        for h in self.hotspots:
            self.world.move_bounce(h, dt)


class World:
    def __init__(self, seed: int, fleet: int, demand: str = "synthetic", tlc=None):
        self.seed, self.fleet = seed, fleet
        self.rng = random.Random(seed)
        self.t = 0.0
        self.demand_mult = 1.0
        self.next_req = 1
        self.outage_seq = 0
        self.event_seq = 0
        self.events: list[Event] = []
        self.listeners = []
        self.city = G.current()
        WX, WZ = self.city.WX, self.city.WZ
        self.heat_cell = max(WX, WZ) / 54                # demand heat map: square cells, as many as fit the city
        self.HEAT_W, self.HEAT_H = math.ceil(WX / self.heat_cell), math.ceil(WZ / self.heat_cell)
        self.HEAT = self.HEAT_W                          # kept for the grid city, where W == H
        self.heat = [0.0] * (self.HEAT_W * self.HEAT_H)
        r = self.rng
        self.blobs = [Blob(*self.city.random_point(r), (r.random() - 0.5) * 0.05, (r.random() - 0.5) * 0.05,
                           22 + r.random() * 20, 0.35 + r.random() * 0.25) for _ in range(4)]
        if not self.city.ambient_congestion:
            self.blobs = []                              # real hourly traffic replaces synthetic congestion
        self.spikes: list[Hotspot] = []
        self.outages: list[Outage] = []
        self.source = tlc if tlc is not None else SyntheticDemand(self)
        if tlc is not None:
            tlc.attach(self)
        self.spike_boost = 0.0
        self.traffic = load_traffic() if tlc is not None else None

    # ---- time, traffic ----------------------------------------------------
    def clock(self) -> float:
        return C.START_CLOCK + self.t

    def rush_factor(self) -> float:
        h = (self.clock() / 3600) % 24
        return 1 + 0.35 * math.exp(-((h - 8.75) ** 2) / (2 * 0.8 ** 2)) + 0.12 * math.sin(self.t / 900)

    def congestion(self, x: float, z: float) -> float:
        m = 1.0
        for b in self.blobs:
            dx, dz = x - b.x, z - b.z
            m *= 1 - b.s * math.exp(-(dx * dx + dz * dz) / (2 * b.r * b.r))
        return max(0.22, m * self.traffic_factor())

    def traffic_factor(self) -> float:
        """Citywide speed multiplier for the hour: measured from real NYC trips when available."""
        if self.traffic is None:
            return 1.05 - 0.15 * (self.rush_factor() - 1)
        h = (self.clock() / 3600) % 24
        i, f = int(h), h - int(h)
        return self.traffic[i] * (1 - f) + self.traffic[(i + 1) % 24] * f

    def rate(self) -> float:
        return self.source.base_rate() * (1 + self.spike_boost)

    # ---- helpers -------------------------------------------------------------
    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, round(self.rng.gauss(lam, math.sqrt(lam))))
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= self.rng.random()
            if p <= L:
                return k - 1

    def move_bounce(self, h, dt):
        h.x += h.vx * dt
        h.z += h.vz * dt
        if h.x < 10 or h.x > self.city.WX - 10:
            h.vx *= -1
        if h.z < 10 or h.z > self.city.WZ - 10:
            h.vz *= -1

    def sample_from(self, spots, p_hot):
        r = self.rng
        if spots and r.random() < p_hot:
            tot = sum(h.w for h in spots)
            pick, hs = r.random() * tot, spots[0]
            for h in spots:
                pick -= h.w
                if pick <= 0:
                    hs = h
                    break
            return clamp(hs.x + r.gauss(0, hs.s), 0, self.city.WX), clamp(hs.z + r.gauss(0, hs.s), 0, self.city.WZ)
        return self.city.random_point(r)

    def random_dropoff(self, x, z):
        r = self.rng
        d = 20 - math.log(1 - r.random()) * 45
        a = r.random() * math.tau
        return clamp(x + math.cos(a) * d, 0, self.city.WX), clamp(z + math.sin(a) * d, 0, self.city.WZ)

    def inner_point(self, margin: float) -> tuple[float, float]:
        """A road point away from the city's edges (for hotspots and incidents)."""
        r, c = self.rng, self.city
        return c.snap(c.WX * (margin + (1 - 2 * margin) * r.random()), c.WZ * (margin + (1 - 2 * margin) * r.random()))

    def make_request(self, x, z, dx, dz) -> Request:
        c = self.city
        px, pz = c.snap(x, z)
        qx, qz = c.snap(dx, dz)
        if qx == px and qz == pz:
            qx, qz = c.snap(px - 30 if px > c.WX / 2 else px + 30, pz)
        req = Request(self.next_req, self.t, px, pz, qx, qz, self.rng.random() < 0.08, zone_of(px, pz))
        self.next_req += 1
        HW, HH = self.HEAT_W, self.HEAT_H
        hx, hz = int(px / self.heat_cell), int(pz / self.heat_cell)
        for oz in range(-2, 3):
            for ox in range(-2, 3):
                X, Z = hx + ox, hz + oz
                if 0 <= X < HW and 0 <= Z < HH:
                    self.heat[Z * HW + X] += math.exp(-(ox * ox + oz * oz) / 2.2)
        return req

    # ---- step ----------------------------------------------------------------
    def step(self, dt: float) -> list[Request]:
        self.t += dt
        t = self.t
        for b in self.blobs:
            self.move_bounce(b, dt)
        self.source.drift(dt)
        self.blobs = [b for b in self.blobs if b.until > t]
        self.spikes = [s for s in self.spikes if s.until > t]
        self.outages = [o for o in self.outages if o.until > t]
        self.spike_boost = sum(s.boost for s in self.spikes)
        dec = math.exp(-dt / 200)
        self.heat = [v * dec for v in self.heat]
        raw = self.source.step(dt)
        if self.spikes:
            extra = self.poisson(self.source.base_rate() * self.spike_boost * dt)
            for _ in range(extra):
                x, z = self.sample_from(self.spikes, 0.9)
                raw.append((x, z, *self.random_dropoff(x, z)))
        return [self.make_request(*q) for q in raw]

    # ---- incidents -------------------------------------------------------------
    def log(self, type_: str, msg: str):
        self.event_seq += 1
        e = Event(self.event_seq, self.t, self.clock(), type_, msg)
        self.events.append(e)
        if len(self.events) > 200:
            self.events.pop(0)
        for f in self.listeners:
            f(e)
        return e

    def _spot(self):
        return self.inner_point(0.2)

    def trigger_spike(self):
        x, z = self._spot()
        self.spikes.append(Hotspot(x, z, 0, 0, 16, 1.0, self.t + 600, True, 0.55))
        self.log("spike", f"Demand spike near {zone_name(zone_of(x, z))}: 55% more requests citywide for 10 min")

    def trigger_outage(self):
        x, z = self._spot()
        self.outage_seq += 1
        self.outages.append(Outage(self.outage_seq, x, z, 55, self.t + 420))
        self.log("outage", f"Network outage around {zone_name(zone_of(x, z))}: cars inside stop reporting GPS")

    def trigger_accident(self):
        x, z = self._spot()
        self.blobs.append(Blob(x, z, 0, 0, 20, 0.78, self.t + 900, True))
        self.log("accident", f"Major accident near {zone_name(zone_of(x, z))}: road speed down about 75%")
