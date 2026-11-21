"""Public EV charging stations of a city, from OpenStreetMap, snapped to its street network.

    python -m orca.chargers --city manhattan        # writes artifacts/chargers_<city>.json (committed)

Every ``amenity=charging_station`` inside the city's box becomes a station at the nearest pickup
intersection (stations more than about 450 m from the network are dropped). Plugs: the
``capacity`` tag, else the sum of socket counts, else 2. Power: the largest tagged output, else
100 kW for a station with a DC fast socket (CCS, CHAdeMO, Tesla Supercharger) and 11 kW otherwise.
"""
from __future__ import annotations

import argparse
import json
import re

import numpy as np

from . import cityspec

DC_SOCKETS = ("type1_combo", "type2_combo", "ccs", "chademo", "tesla_supercharger")
MAX_SNAP = 15.0                      # world units (~450 m)


def path(city: str):
    return cityspec.ARTIFACTS / f"chargers_{city}.json"


def load(city) -> dict | None:
    """Station arrays for a built city (x, z, node, plugs, kw, dc, name), or None if not fetched."""
    p = path(city.name)
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    st = d["stations"]
    return dict(x=np.array([s["x"] for s in st], dtype=float), z=np.array([s["z"] for s in st], dtype=float),
                node=np.array([s["node"] for s in st], dtype=np.int64), plugs=np.array([s["plugs"] for s in st], dtype=np.int64),
                kw=np.array([s["kw"] for s in st], dtype=float), dc=np.array([s["dc"] for s in st], dtype=bool),
                name=[s["name"] for s in st])


def _kw(tags: dict) -> float | None:
    best = None
    for k, v in tags.items():
        if k.endswith(":output") or k == "charging_station:output":
            for num, unit in re.findall(r"([\d.]+)\s*(kw|w|kva)?", v.lower()):
                try:
                    val = float(num) / (1000 if unit == "w" else 1)
                except ValueError:
                    continue
                if 1 <= val <= 400:
                    best = max(best or 0, val)
    return best


def _plugs(tags: dict) -> int:
    try:
        return max(1, min(40, int(tags["capacity"])))
    except (KeyError, ValueError):
        pass
    n = 0
    for k, v in tags.items():
        if k.startswith("socket:") and k.count(":") == 1:
            try:
                n += int(v)
            except ValueError:
                n += 1
    return max(1, min(40, n)) if n else 2


def build(name: str) -> dict:
    from . import city as G
    from .demand import StreetProjector
    from .osm import bbox_filter, query
    spec = cityspec.get(name)
    city = G.use(name)
    bb = bbox_filter(spec.bbox)
    data = query(f'[out:json][timeout:120];(node["amenity"="charging_station"]{bb};way["amenity"="charging_station"]{bb};);'
                 "out center tags;", timeout=180)
    proj = StreetProjector(city)
    stations, by_node = [], {}
    for el in data["elements"]:
        lat, lon = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"])
        tags = el.get("tags", {})
        e, n = spec.project(np.array([lon]), np.array([lat]))
        x, z = (float(v[0]) for v in proj(e, n))
        node = city.nearest_pickup(x, z)
        if np.hypot(city.nx[node] - x, city.nz[node] - z) > MAX_SNAP:
            continue
        dc = any(k.startswith("socket:") and any(t in k for t in DC_SOCKETS) for k in tags) or "supercharger" in tags.get("name", "").lower()
        kw = _kw(tags) or (100.0 if dc else 11.0)
        plugs = _plugs(tags)
        if node in by_node:                                  # two tagged stations at one corner: one bigger station
            s = by_node[node]
            s["plugs"] += plugs
            s["kw"], s["dc"] = max(s["kw"], kw), s["dc"] or dc
            continue
        s = dict(node=int(node), x=float(city.nx[node]), z=float(city.nz[node]), plugs=plugs, kw=kw, dc=bool(dc),
                 name=tags.get("name") or tags.get("operator") or "Charging station")
        by_node[node] = s
        stations.append(s)
    out = dict(city=name, source="OpenStreetMap amenity=charging_station", fetched=len(data["elements"]), stations=stations)
    path(name).write_text(json.dumps(out, indent=1))
    dc = [s for s in stations if s["dc"]]
    print(f"{name}: {len(stations)} stations on the network ({len(dc)} DC fast, {sum(s['plugs'] for s in dc)} fast plugs, "
          f"{sum(s['plugs'] for s in stations)} plugs in all) from {len(data['elements'])} in OpenStreetMap")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", nargs="+", default=None)
    for c in ap.parse_args().city or cityspec.built():
        build(c)
