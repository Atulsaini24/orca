"""Real-time feature store, built only from what a dispatch system can observe.

* Zone speeds: estimated from consecutive GPS pings of units that are driving a
  job (pickup or trip legs), smoothed with an EWMA per zone.
* Zone demand: pickups per zone per minute, kept as a short rolling history for
  the demand forecaster.

The ETA and demand models read features from here, never from the simulator's
hidden ground truth (the true traffic field).
"""
from __future__ import annotations

import math
from collections import deque

from . import config as C
from .city import arterial_fraction, manhattan, zone_of

N_ZONES = C.ZONES * C.ZONES
HISTORY = 30  # minutes of per-zone demand history


class FeatureStore:
    def __init__(self):
        self.speed = [C.BASE_SPEED * 1.1] * N_ZONES     # units / s, observed
        self.speed_n = [0] * N_ZONES
        self.minute = [0] * N_ZONES                       # pickups in the current minute
        self.hist = deque(maxlen=HISTORY)                 # list of per-zone counts, oldest first
        self.city_speed = C.BASE_SPEED * 1.1

    # ---- telemetry -------------------------------------------------------------
    def observe_ping(self, x0, z0, x1, z1, dt):
        if dt <= 0.5:
            return
        d = abs(x1 - x0) + abs(z1 - z0)
        v = d / dt
        if v <= 0.02:      # stopped; not informative about road speed
            return
        zi = zone_of((x0 + x1) / 2, (z0 + z1) / 2)
        self.speed[zi] = 0.9 * self.speed[zi] + 0.1 * v
        self.speed_n[zi] += 1
        self.city_speed = 0.995 * self.city_speed + 0.005 * v

    def observe_request(self, zone: int):
        self.minute[zone] += 1

    def roll_minute(self):
        self.hist.append(self.minute)
        self.minute = [0] * N_ZONES

    # ---- features ----------------------------------------------------------------
    def zone_speed(self, x, z) -> float:
        zi = zone_of(x, z)
        return self.speed[zi] if self.speed_n[zi] >= 3 else self.city_speed

    def eta_features(self, ax, az, bx, bz, clock) -> list[float]:
        d = manhattan(ax, az, bx, bz)
        hour = (clock / 3600) % 24
        return [
            d,
            math.hypot(bx - ax, bz - az),
            arterial_fraction(ax, az, bx, bz),
            self.zone_speed(ax, az),
            self.zone_speed(bx, az),
            self.zone_speed(bx, bz),
            self.city_speed,
            math.sin(hour / 24 * math.tau),
            math.cos(hour / 24 * math.tau),
        ]

    def demand_features(self, zone: int, clock: float) -> list[float]:
        h = list(self.hist)
        lag = lambda k: h[-k][zone] if len(h) >= k else 0.0
        mean15 = sum(m[zone] for m in h[-15:]) / max(1, len(h[-15:]))
        city1 = sum(h[-1]) if h else 0.0
        city5 = sum(sum(m) for m in h[-5:]) / max(1, len(h[-5:]))
        minute = (clock / 60) % 1440
        return [lag(1), lag(2), lag(3), lag(4), lag(5), mean15, city1, city5,
                math.sin(minute / 1440 * math.tau), math.cos(minute / 1440 * math.tau),
                zone % C.ZONES, zone // C.ZONES]


ETA_FEATURES = ["manhattan", "euclid", "arterial_frac", "speed_origin", "speed_corner", "speed_dest",
                "speed_city", "hour_sin", "hour_cos"]
DEMAND_FEATURES = ["lag1", "lag2", "lag3", "lag4", "lag5", "mean15", "city_lag1", "city_mean5",
                   "minute_sin", "minute_cos", "zone_x", "zone_z"]
