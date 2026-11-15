"""Road grid geometry, routing and zones."""
from __future__ import annotations

import math

from .config import CELL, GRID, ZONES, ZSIZE, W


def is_arterial(i: float) -> bool:
    if i != int(i):
        return False
    i = int(i)
    return i % 7 == 0 or i == GRID - 1


def clamp(v, a, b):
    return a if v < a else b if v > b else v


def snap(v: float) -> float:
    """Snap a coordinate to the nearest road line."""
    return float(clamp(round(v / CELL), 0, GRID - 1) * CELL)


def zone_of(x: float, z: float) -> int:
    zx = clamp(int(x // ZSIZE), 0, ZONES - 1)
    zz = clamp(int(z // ZSIZE), 0, ZONES - 1)
    return zz * ZONES + zx


def zone_name(i: int) -> str:
    return chr(65 + i % ZONES) + str(1 + i // ZONES)


def zone_center(i: int) -> tuple[float, float]:
    return ((i % ZONES + 0.5) * ZSIZE, (i // ZONES + 0.5) * ZSIZE)


def zone_dist(a: int, b: int) -> float:
    return math.hypot(a % ZONES - b % ZONES, a // ZONES - b // ZONES)


def on_road(x: float, z: float) -> bool:
    return x % CELL == 0 or z % CELL == 0


def route(x: float, z: float, tx: float, tz: float, agent_id: int = 0) -> list[tuple[float, float]]:
    """Axis-aligned route from a point on a road to an intersection.

    First reach the nearest intersection in the direction of travel, then take
    an L-shaped path, preferring to run along an arterial when one is available.
    Every leg changes exactly one coordinate, so movement stays on the roads.
    """
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
    x_first = is_arterial(z / CELL) or (not is_arterial(x / CELL) and (agent_id + int((tx + tz) / CELL)) % 2 == 0)
    if x_first:
        if x != tx:
            pts.append((tx, z))
        if z != tz:
            pts.append((tx, tz))
    else:
        if z != tz:
            pts.append((x, tz))
        if x != tx:
            pts.append((tx, tz))
    return pts


def manhattan(ax, az, bx, bz) -> float:
    return abs(bx - ax) + abs(bz - az)


def arterial_fraction(ax, az, bx, bz) -> float:
    """Rough share of an L-route that can run along arterial roads."""
    d = manhattan(ax, az, bx, bz)
    if d == 0:
        return 0.0
    art = 0.0
    if is_arterial(round(az / CELL)) or is_arterial(round(bz / CELL)):
        art += abs(bx - ax)
    if is_arterial(round(ax / CELL)) or is_arterial(round(bx / CELL)):
        art += abs(bz - az)
    return min(1.0, art / d)


WORLD = W
