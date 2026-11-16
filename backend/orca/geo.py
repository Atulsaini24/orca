"""Map projections for New York City data, without external GIS libraries.

* ``to_state_plane``: WGS84 / NAD83 longitude-latitude to New York State Plane, Long Island
  zone (EPSG:2263, the frame of the TLC taxi-zone shapefile), in metres. Lambert Conformal
  Conic with two standard parallels on the GRS80 ellipsoid (Snyder, *Map Projections: A
  Working Manual*, USGS 1987, eqs. 15-1 to 15-10). NAD83 and WGS84 differ by about a metre
  here, far below what a city-scale simulation resolves.
* ``Frame``: state-plane metres to ORCA world units, rotated so Manhattan's avenues run along
  the z axis (the street grid sits about 29 degrees east of true north) with north at -z.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

A = 6378137.0                     # GRS80 semi-major axis (m)
F = 1 / 298.257222101
E = math.sqrt(2 * F - F * F)      # eccentricity
FT = 1200 / 3937                  # US survey foot in metres
LAT1, LAT2, LAT0, LON0 = map(math.radians, (41.0 + 2 / 60, 40.0 + 40 / 60, 40.0 + 10 / 60, -74.0))
FE, FN = 984250.0 * FT, 0.0       # false easting / northing (m)


def _m(phi):
    return np.cos(phi) / np.sqrt(1 - (E * np.sin(phi)) ** 2)


def _t(phi):
    s = np.sin(phi)
    return np.tan(np.pi / 4 - phi / 2) / ((1 - E * s) / (1 + E * s)) ** (E / 2)


_n = (math.log(_m(LAT1)) - math.log(_m(LAT2))) / (math.log(_t(LAT1)) - math.log(_t(LAT2)))
_F = _m(LAT1) / (_n * _t(LAT1) ** _n)
_rho0 = A * _F * _t(LAT0) ** _n


def to_state_plane(lon, lat):
    """Longitude / latitude in degrees -> EPSG:2263 easting / northing in metres."""
    phi, lam = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    rho = A * _F * _t(phi) ** _n
    theta = _n * (lam - LON0)
    return FE + rho * np.sin(theta), FN + _rho0 - rho * np.cos(theta)


MANHATTAN_GRID_DEG = 29.0
UNIT_M = 30.0                     # one world unit is 30 m, as in the grid city


@dataclass(frozen=True)
class Frame:
    """State-plane metres -> world units: rotate by the street-grid angle, scale, offset."""

    e0: float
    n0: float
    angle_deg: float = MANHATTAN_GRID_DEG
    unit_m: float = UNIT_M

    def to_world(self, e, n):
        a = math.radians(self.angle_deg)
        de, dn = np.asarray(e, dtype=float) - self.e0, np.asarray(n, dtype=float) - self.n0
        u = de * math.cos(a) - dn * math.sin(a)          # across the island (avenues are constant u)
        v = de * math.sin(a) + dn * math.cos(a)          # along the island, uptown positive
        return u / self.unit_m, -v / self.unit_m         # world x, z (north = -z, as in the grid city)


# ---------------------------------------------------------------------------------------------------
# Transverse Mercator (Snyder 1987, eqs. 8-9 to 8-10), for state-plane zones such as Illinois East.
def _tm_forward(lon, lat, lat0, lon0, k0, fe, fn):
    e2 = 2 * F - F * F
    ep2 = e2 / (1 - e2)
    phi, lam = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    phi0, lam0 = math.radians(lat0), math.radians(lon0)

    def M(p):
        return A * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * p
                    - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * np.sin(2 * p)
                    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * np.sin(4 * p)
                    - (35 * e2 ** 3 / 3072) * np.sin(6 * p))
    N = A / np.sqrt(1 - e2 * np.sin(phi) ** 2)
    T = np.tan(phi) ** 2
    C = ep2 * np.cos(phi) ** 2
    Aa = (lam - lam0) * np.cos(phi)
    x = k0 * N * (Aa + (1 - T + C) * Aa ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * Aa ** 5 / 120)
    y = k0 * (M(phi) - M(phi0) + N * np.tan(phi) * (Aa ** 2 / 2 + (5 - T + 9 * C + 4 * C * C) * Aa ** 4 / 24
                                                   + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * Aa ** 6 / 720))
    return fe + x, fn + y


def to_il_east(lon, lat):
    """Longitude / latitude in degrees -> Illinois State Plane East (EPSG:3435) in metres."""
    return _tm_forward(lon, lat, 36 + 40 / 60, -(88 + 20 / 60), 0.999975, 984250.0 * FT, 0.0)


# Each city: its working projection (shapefile frame) and the angle of its street grid from true north.
PROJECTIONS = {"manhattan": (to_state_plane, 29.0), "chicago": (to_il_east, 0.0)}


def project(city: str, lon, lat):
    return PROJECTIONS[city][0](lon, lat)
