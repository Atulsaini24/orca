"""The city the engine runs on: road network, routing, zones.

Two interchangeable implementations behind one API:

* ``StreetCity`` (orca/streets.py): Manhattan's real street network from OpenStreetMap, the
  63 official TLC taxi zones, one-way streets, road classes, shortest paths by travel time.
  This is the production city.
* ``GridCity`` (below): a synthetic 28 x 28 grid with arterials every seventh road. Small and
  fully predictable, used by unit tests and as a fallback when the street data is absent.

The engine calls the module-level functions (``zone_of``, ``road_metrics``, ...) or the active
city object (``current()``). ``use(name)`` switches it; one city is active per process.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from . import config as C
from .config import CELL, GRID, ZONES, ZSIZE

STREETS_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "streets_manhattan.npz"


def clamp(v, a, b):
    return a if v < a else b if v > b else v


def is_arterial(i: float) -> bool:
    if i != int(i):
        return False
    i = int(i)
    return i % 7 == 0 or i == GRID - 1


def manhattan(ax, az, bx, bz) -> float:
    return abs(bx - ax) + abs(bz - az)


class GridCity:
    """Synthetic grid: roads every CELL units, arterials every seventh road, 7 x 7 square zones."""

    name = "grid"
    label = "synthetic 28 x 28 grid"

    def __init__(self):
        self.WX = self.WZ = float(C.W)
        self.N_ZONES = ZONES * ZONES
        self.base_speed = C.BASE_SPEED
        self.ambient_congestion = True             # drifting congestion blobs stand in for traffic
        self.demand_scale = 1.0                    # synthetic demand per car (tests): calibrated for this city
        self.zone_names = [chr(65 + i % ZONES) + str(1 + i // ZONES) for i in range(self.N_ZONES)]

    # ---- geometry
    def snap(self, x: float, z: float) -> tuple[float, float]:
        s = lambda v: float(clamp(round(v / CELL), 0, GRID - 1) * CELL)
        return s(x), s(z)

    def on_road(self, x: float, z: float) -> bool:
        return x % CELL == 0 or z % CELL == 0

    def random_point(self, rng) -> tuple[float, float]:
        if rng.random() < 0.5:
            return rng.random() * self.WX, float(rng.randrange(GRID) * CELL)
        return float(rng.randrange(GRID) * CELL), rng.random() * self.WZ

    def point_in_zone(self, zi: int, rng) -> tuple[float, float]:
        zx, zz = zi % ZONES, zi // ZONES
        return self.snap((zx + 0.2 + 0.6 * rng.random()) * ZSIZE, (zz + 0.2 + 0.6 * rng.random()) * ZSIZE)

    # ---- zones
    def zone_of(self, x: float, z: float) -> int:
        zx = clamp(int(x // ZSIZE), 0, ZONES - 1)
        zz = clamp(int(z // ZSIZE), 0, ZONES - 1)
        return zz * ZONES + zx

    def zone_center(self, i: int) -> tuple[float, float]:
        return ((i % ZONES + 0.5) * ZSIZE, (i // ZONES + 0.5) * ZSIZE)

    def zone_dist(self, a: int, b: int) -> float:
        return math.hypot(a % ZONES - b % ZONES, a // ZONES - b // ZONES)

    # ---- routing: waypoints (x, z, speed multiplier for the leg ending there, node id or -1)
    def route(self, agent, tx: float, tz: float) -> list[tuple]:
        x, z = agent.x, agent.z
        pts: list[tuple[float, float]] = []
        on_v, on_h = x % CELL == 0, z % CELL == 0
        if not (on_v and on_h):
            if on_h:
                cx = math.ceil(x / CELL) * CELL if tx > x else math.floor(x / CELL) * CELL
                pts.append((float(cx), z))
                x = float(cx)
            else:
                cz = math.ceil(z / CELL) * CELL if tz > z else math.floor(z / CELL) * CELL
                pts.append((x, float(cz)))
                z = float(cz)
        x_first = is_arterial(z / CELL) or (not is_arterial(x / CELL) and (agent.id + int((tx + tz) / CELL)) % 2 == 0)
        legs = [(tx, z), (tx, tz)] if x_first else [(x, tz), (tx, tz)]
        for p in legs:
            if p != (pts[-1] if pts else (agent.x, agent.z)):
                pts.append(p)
        out, px, pz = [], agent.x, agent.z
        for wx, wz in pts:
            line = pz / CELL if wx != px else px / CELL
            mult = C.ART_MULT if line == int(line) and is_arterial(line) else 1.0
            out.append((wx, wz, mult, -1))
            px, pz = wx, wz
        return out

    def cruise(self, agent, rng) -> list[tuple]:
        r = rng.random
        tx, tz = self.snap(agent.x + round((r() - 0.5) * 4) * CELL, agent.z + round((r() - 0.5) * 4) * CELL)
        return self.route(agent, tx, tz)

    def road_metrics(self, ax, az, bx, bz) -> tuple[float, float]:
        """(road distance, share on major roads) between two points."""
        d = manhattan(ax, az, bx, bz)
        if d == 0:
            return 0.0, 0.0
        art = 0.0
        if is_arterial(round(az / CELL)) or is_arterial(round(bz / CELL)):
            art += abs(bx - ax)
        if is_arterial(round(ax / CELL)) or is_arterial(round(bx / CELL)):
            art += abs(bz - az)
        return d, min(1.0, art / d)

    def street_name(self, x, z) -> str:
        return ""


_CITY = None


def available() -> list[str]:
    """Built real cities (registry order: Manhattan first), then the synthetic grid."""
    from . import cityspec
    return [*cityspec.built(), "grid"]


def use(name: str | None = None):
    """Activate a city by name ('manhattan', 'chicago', ... or 'grid'); default from ORCA_CITY, else the first built."""
    global _CITY
    name = name or os.environ.get("ORCA_CITY") or available()[0]
    if _CITY is not None and _CITY.name == name:
        return _CITY
    from . import cityspec
    if name in cityspec.CITIES and cityspec.get(name).streets.exists():
        from .streets import StreetCity
        _CITY = StreetCity.load(cityspec.get(name).streets)
    elif name == "grid":
        _CITY = GridCity()
    else:
        raise ValueError(f"unknown city '{name}' (choose from {available()})")
    C.TUNED = C.load_tuned(name)                       # scoring weights tuned for this city, if any
    C.apply({**C.DEFAULTS, **C.TUNED})
    return _CITY


def current():
    return _CITY if _CITY is not None else use()


# ---- module-level API used by the engine ---------------------------------------------------------
def snap(x, z):
    return current().snap(x, z)


def zone_of(x, z):
    return current().zone_of(x, z)


def zone_name(i):
    return current().zone_names[i]


def zone_center(i):
    return current().zone_center(i)


def zone_dist(a, b):
    return current().zone_dist(a, b)


def n_zones() -> int:
    return current().N_ZONES


def road_metrics(ax, az, bx, bz):
    return current().road_metrics(ax, az, bx, bz)
