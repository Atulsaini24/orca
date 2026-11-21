"""Registry of the real cities ORCA can run, and where each one's data lives.

A city is defined by: its working projection (the frame its official zone shapefile uses), the
angle of its street grid, its forecasting zones (official polygons), the bounding box for
OpenStreetMap roads, and its trip data. Everything downstream (street network, skyline, trip
replay, models, benchmark) is built per city from this entry.

    python scripts/build_streets.py --city chicago
    python scripts/build_buildings.py --city chicago
    python scripts/prepare_trips.py --city chicago
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
FT = 1200 / 3937                       # US survey foot in metres (both state-plane shapefiles use it)


@dataclass(frozen=True)
class CitySpec:
    name: str
    label: str
    short: str
    projection: str                    # key into orca.geo.PROJECTIONS, or "local:<lat0>,<lon0>"
    grid_angle: float                  # degrees east of north of the street grid
    bbox: tuple                        # south, west, north, east (degrees) for OpenStreetMap roads
    zone_label: str
    data_note: str
    demand_source: str
    tags: dict = field(default_factory=dict)

    # ---- files
    @property
    def osm_roads(self) -> Path:
        return DATA / "osm" / ("manhattan_roads.json" if self.name == "manhattan" else f"{self.name}_roads.json")

    @property
    def streets(self) -> Path:
        return ARTIFACTS / f"streets_{self.name}.npz"

    @property
    def trips(self) -> Path:
        return DATA / f"trips_{self.name}.parquet"

    @property
    def traffic(self) -> Path:
        return ARTIFACTS / f"traffic_{self.name}.json"

    @property
    def meta(self) -> Path:
        return ARTIFACTS / f"city_{self.name}.json"

    def project(self, lon, lat):
        from .geo import project
        return project(self.projection, lon, lat)

    # ---- zones: {zone id: (list of rings in projected metres, name)}
    def zones(self) -> dict:
        if self.name in ZONE_LOADERS:
            return ZONE_LOADERS[self.name]()
        d = json.loads((CUSTOM / f"{self.name}_zones.json").read_text())       # built by orca.newcity
        return {int(z["id"]): ([np.asarray(r, dtype=float) for r in z["rings"]], z["name"]) for z in d["zones"]}


def _manhattan_zones() -> dict:
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import prepare_tlc as P
    zones = P.zone_polygons(DATA / "taxi_zones" / "taxi_zones" / "taxi_zones.shp")
    return {z: ([r * FT for r in rings], name) for z, (rings, name) in zones.items()}


def _chicago_zones() -> dict:
    """Official neighbourhoods (City of Chicago, 2012) whose centre lies in the seven core community areas."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import zone_centroids as zc
    from matplotlib.path import Path as MPath

    from .geo import to_il_east
    region = json.loads((DATA / "chicago" / "region.json").read_text())
    cas = json.loads((DATA / "chicago" / "community_areas.geojson").read_text())
    ca_paths = []
    for f in cas["features"]:
        if int(f["properties"]["area_numbe"]) not in region["cas"]:
            continue
        g = f["geometry"]
        for poly in g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]:
            ring = np.asarray(poly[0], dtype=float)
            x, y = to_il_east(ring[:, 0], ring[:, 1])
            ca_paths.append(MPath(np.c_[x, y]))
    shp = DATA / "chicago" / "neighborhoods" / "Neighborhoods_2012b.shp"
    shapes, attrs = zc.read_shp(shp), zc.read_dbf(shp.with_suffix(".dbf"))
    out = {}
    for i, (s, a) in enumerate(zip(shapes, attrs)):
        if s is None:
            continue
        pts, parts = s
        bounds = [*list(parts), len(pts)]
        rings = [np.asarray(pts[bounds[k]:bounds[k + 1]], dtype=float) * FT for k in range(len(parts))]
        cx, cy = zc.polygon_centroid(pts, parts)
        if any(p.contains_point((cx * FT, cy * FT)) for p in ca_paths):
            out[i + 1] = (rings, a.get("PRI_NEIGH", f"Zone {i + 1}").strip())
    return out


ZONE_LOADERS = {"manhattan": _manhattan_zones, "chicago": _chicago_zones}


def _chicago_bbox():
    p = DATA / "chicago" / "region.json"
    if p.exists():
        s, w, n, e = json.loads(p.read_text())["bbox"]
        return (s - 0.004, w - 0.004, n + 0.004, e + 0.004)
    return (41.8407, -87.711, 41.9656, -87.5945)


CITIES = {
    "manhattan": CitySpec(
        "manhattan", "Manhattan, New York", "Manhattan", "manhattan", 29.0, (40.698, -74.021, 40.882, -73.906),
        "official TLC taxi zones",
        "NYC Taxi & Limousine Commission: yellow taxi and Uber / Lyft (high-volume for-hire) trips, September 2026",
        "NYC TLC trip records"),
    "chicago": CitySpec(
        "chicago", "Chicago, downtown core", "Chicago", "chicago", 0.0, _chicago_bbox(),
        "official Chicago neighbourhoods",
        "City of Chicago: Transportation Network Providers (Uber, Lyft, Via) trips, September 2026",
        "City of Chicago TNP trip records", tags={"surround": "lake-east"}),
}


CUSTOM = ARTIFACTS / "cities"         # cities built with `python -m orca.newcity` (spec + zones as JSON)


def _load_custom():
    if not CUSTOM.is_dir():
        return
    for p in sorted(CUSTOM.glob("*.json")):
        if p.name.endswith("_zones.json"):
            continue
        d = json.loads(p.read_text())
        CITIES.setdefault(d["name"], CitySpec(d["name"], d["label"], d["short"], d["projection"], float(d["grid_angle"]),
                                              tuple(d["bbox"]), d["zone_label"], d["data_note"], d["demand_source"],
                                              tags=d.get("tags", {})))


_load_custom()


def get(name: str) -> CitySpec:
    if name not in CITIES:
        raise KeyError(f"unknown city '{name}'; known: {', '.join(CITIES)}")
    return CITIES[name]


def built() -> list[str]:
    """Cities whose street network has been built, in registry order."""
    return [n for n, c in CITIES.items() if c.streets.exists()]


def trips_file(name: str | None) -> Path | None:
    """The prepared real-trip replay for a city (scripts/prepare_trips.py), or None if it is not built."""
    if name not in CITIES:
        return None
    p = CITIES[name].trips
    if p.exists():
        return p
    old = DATA / "nyc_tlc_2015-01_manhattan.parquet"         # the earlier, January 2015 replay
    return old if name == "manhattan" and old.exists() else None
