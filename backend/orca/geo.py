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
