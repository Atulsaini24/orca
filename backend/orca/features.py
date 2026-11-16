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

import numpy as np

from . import city as G
from .city import zone_of

HISTORY = 30  # minutes of per-zone demand history


class FeatureStore:
    def __init__(self):
        city = G.current()
        self.city_obj = city
        self.n_zones = city.N_ZONES
        # observed speeds live in arrays the fleet kernel updates in place (orca/kernels.py, step_fleet)
        self.speed = np.full(self.n_zones, city.base_speed * 1.1)     # units / s, observed
        self.speed_n = np.zeros(self.n_zones, dtype=np.int64)
        self.city = np.array([city.base_speed * 1.1])
        self.minute = [0] * self.n_zones                        # pickups in the current minute
        self.hist = deque(maxlen=HISTORY)                       # list of per-zone counts, oldest first
        # zone position features: zone centre, scaled so the city's long side spans 0-10
        s = 10 / max(city.WX, city.WZ)
        self.zone_xy = [(cx * s, cz * s) for cx, cz in (city.zone_center(i) for i in range(self.n_zones))]

    @property
    def city_speed(self) -> float:
        return float(self.city[0])

    # ---- telemetry -------------------------------------------------------------
    def observe_request(self, zone: int):
        self.minute[zone] += 1

    def roll_minute(self):
        self.hist.append(self.minute)
        self.minute = [0] * self.n_zones

    # ---- features ----------------------------------------------------------------
    def zone_speed(self, x, z) -> float:
        zi = zone_of(x, z)
        return float(self.speed[zi]) if self.speed_n[zi] >= 3 else self.city_speed

    def zone_speed_many(self, x, z) -> np.ndarray:
        zi = self.city_obj.zone_of_many(x, z)
        return np.where(self.speed_n[zi] >= 3, self.speed[zi], self.city[0])

    def eta_features_many(self, ax, az, bx, bz, clock) -> np.ndarray:
        """eta_features for many pairs at once (same columns, ETA_FEATURES)."""
        d, major = self.city_obj.road_metrics_many(ax, az, bx, bz)
        hour = (clock / 3600) % 24
        n = len(d)
        return np.column_stack([d, np.hypot(np.subtract(bx, ax), np.subtract(bz, az)), major,
                                self.zone_speed_many(ax, az), self.zone_speed_many(bx, az), self.zone_speed_many(bx, bz),
                                np.full(n, self.city[0]), np.full(n, math.sin(hour / 24 * math.tau)),
                                np.full(n, math.cos(hour / 24 * math.tau))])

    def eta_features(self, ax, az, bx, bz, clock) -> list[float]:
        d, major = G.road_metrics(ax, az, bx, bz)
        hour = (clock / 3600) % 24
        return [
            d,
            math.hypot(bx - ax, bz - az),
            major,
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
                *self.zone_xy[zone]]


ETA_FEATURES = ["road_dist", "euclid", "arterial_frac", "speed_origin", "speed_corner", "speed_dest",
                "speed_city", "hour_sin", "hour_cos"]
DEMAND_FEATURES = ["lag1", "lag2", "lag3", "lag4", "lag5", "mean15", "city_lag1", "city_mean5",
                   "minute_sin", "minute_cos", "zone_x", "zone_z"]
