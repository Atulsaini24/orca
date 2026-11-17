"""Build any city from OpenStreetMap with one command, then run ORCA on it.

    python -m orca.newcity --name cambridge --place "Cambridge, Massachusetts"
    python -m orca.newcity --name amsterdam --bbox 52.355,4.865,52.385,4.925 --label "Amsterdam, centre"
    python -m orca.server --city cambridge          # then open http://localhost:8000/app

1. Box: --bbox (south,west,north,east) or the place's bounding box from Nominatim, cut to at most
   --max-km on a side around its centre (the street graph and zone lookup are sized for a city centre).
2. Roads and parks from OpenStreetMap (Overpass), the same road classes as the built-in cities.
3. Projection: metres around the box centre (orca.geo.to_local). The street-grid angle is found from
   the roads: the length-weighted dominant bearing of every segment, folded to 90 degrees, so the
   world frame lines up with the city's own grid.
4. Zones: --zone-km squares holding at least --min-nodes road nodes (no zones over water or empty
   land), each named after the OpenStreetMap suburb, quarter or neighbourhood inside or nearest it.
5. The usual builders: street network (scripts/build_streets.py), 3D buildings from OpenStreetMap
   footprints and heights (scripts/build_buildings.py), public chargers (orca.chargers), and the
   control room bundle (frontend/build.py).

Cities built this way have no trip records, so the engine runs synthetic demand and a flat traffic
profile; everything else (routing, dispatch, shared rides, EVs, what-if) works as in the others.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from . import cityspec
from .geo import to_local
from .osm import USER_AGENT, bbox_filter, query

ROOT = Path(__file__).resolve().parents[1]
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
                "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link")
PLACE_RANK = {"suburb": 0, "quarter": 1, "neighbourhood": 2}


def geocode(place: str) -> tuple[float, float, float, float]:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as r:
        hits = json.load(r)
    if not hits:
        raise SystemExit(f"Nominatim found nothing for {place!r}; pass --bbox instead")
    s, n, w, e = (float(v) for v in hits[0]["boundingbox"])
    return s, w, n, e


def clip_box(box, max_km: float) -> tuple[float, float, float, float]:
    """At most max_km on a side, around the box centre."""
    s, w, n, e = box
    lat0, lon0 = (s + n) / 2, (w + e) / 2
    half_lat = min((n - s) / 2, max_km / 2 / 111.32)
    half_lon = min((e - w) / 2, max_km / 2 / (111.32 * math.cos(math.radians(lat0))))
    return lat0 - half_lat, lon0 - half_lon, lat0 + half_lat, lon0 + half_lon


def grid_angle(x0, y0, x1, y1) -> float:
    """Dominant street bearing in degrees east of north, folded to (-45, 45], weighted by segment length."""
    dx, dy = np.asarray(x1) - x0, np.asarray(y1) - y0
    length = np.hypot(dx, dy)
    theta = np.degrees(np.arctan2(dx, dy)) % 90                   # bearing folded to one quadrant
    hist, edges = np.histogram(theta, bins=90, range=(0, 90), weights=length)
    hist = np.convolve(np.r_[hist[-2:], hist, hist[:2]], np.ones(5) / 5, mode="valid")    # circular smoothing
    peak = (edges[int(np.argmax(hist))] + 0.5) % 90
    return float(peak - 90 if peak > 45 else peak)


def make_zones(e, n, places, zone_m: float, min_nodes: int, short: str) -> list[dict]:
    """Squares of zone_m metres holding at least min_nodes road nodes, named after nearby OSM places."""
    e0, n0 = e.min(), n.min()
    ix, iy = ((e - e0) // zone_m).astype(int), ((n - n0) // zone_m).astype(int)
    counts = np.zeros((iy.max() + 1, ix.max() + 1), dtype=int)
    np.add.at(counts, (iy, ix), 1)
    zones, used = [], {}
    rows, cols = np.nonzero(counts >= min_nodes)
    for k, (r, c) in enumerate(zip(rows.tolist(), cols.tolist())):
        x0, y0 = e0 + c * zone_m, n0 + r * zone_m
        cx, cy = x0 + zone_m / 2, y0 + zone_m / 2
        name = None
        if places:
            inside = [p for p in places if x0 <= p[0] < x0 + zone_m and y0 <= p[1] < y0 + zone_m]
            if inside:
                name = min(inside, key=lambda p: (p[3], math.hypot(p[0] - cx, p[1] - cy)))[2]
            else:
                p = min(places, key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
                if math.hypot(p[0] - cx, p[1] - cy) < 1.5 * zone_m:
                    name = p[2]
        name = name or f"{short} {chr(65 + c % 26)}{r + 1}"
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name} {used[name]}"
        zones.append(dict(id=k + 1, name=name,
                          rings=[[[x0, y0], [x0 + zone_m, y0], [x0 + zone_m, y0 + zone_m], [x0, y0 + zone_m]]]))
    return zones


def run(cmd: list[str], cwd: Path):
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="short id: lower-case letters, digits and dashes")
    ap.add_argument("--place", help="a place name to geocode with Nominatim")
    ap.add_argument("--bbox", help="south,west,north,east in degrees")
    ap.add_argument("--label", help='display name, e.g. "Cambridge, Massachusetts"')
    ap.add_argument("--max-km", type=float, default=8.0)
    ap.add_argument("--zone-km", type=float, default=0.9)
    ap.add_argument("--min-nodes", type=int, default=25)
    ap.add_argument("--skip-buildings", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="download the roads again even if they are already here")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", a.name) or a.name in ("grid", "manhattan", "chicago"):
        ap.error("--name must be a new lower-case id such as 'cambridge'")
    if not (a.place or a.bbox):
        ap.error("give --place or --bbox")
    t0 = time.time()
    box = tuple(float(v) for v in a.bbox.split(",")) if a.bbox else geocode(a.place)
    box = clip_box(box, a.max_km)
    s, w, n, e = box
    lat0, lon0 = (s + n) / 2, (w + e) / 2
    label = a.label or (a.place or a.name).split(",")[0].strip() + (", " + a.place.split(",")[1].strip() if a.place and "," in a.place else "")
    short = label.split(",")[0]
    print(f"{label}: box {s:.4f},{w:.4f} to {n:.4f},{e:.4f} "
          f"({(e - w) * 111.32 * math.cos(math.radians(lat0)):.1f} x {(n - s) * 111.32:.1f} km)", flush=True)

    # roads and parks, written where scripts/build_streets.py reads them
    osm_dir = cityspec.DATA / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    roads = osm_dir / f"{a.name}_roads.json"
    q = ('[out:json][timeout:300];way["highway"~"^(' + "|".join(ROAD_CLASSES) + ')$"]["access"!~"^(private|no)$"]["area"!="yes"]'
         f"{bbox_filter(box)};(._;>;);out body qt;")
    if a.refresh or not roads.exists():
        roads.write_bytes(query(q, timeout=400, raw=True))

    def optional(what, q_, timeout):                       # scenery and names: nice to have, never fatal
        try:
            return query(q_, timeout=timeout)
        except RuntimeError as exc:
            print(f"no {what} ({str(exc).splitlines()[0]})", flush=True)
            return {"elements": []}
    parks = optional("parks", f'[out:json][timeout:180];way["leisure"="park"]{bbox_filter(box)};out geom;', 240)
    (osm_dir / f"{a.name}_parks.json").write_text(json.dumps(parks))
    places = optional("place names", f'[out:json][timeout:60];node["place"~"^(suburb|quarter|neighbourhood)$"]{bbox_filter(box)};out;', 120)

    els = json.loads(roads.read_text(encoding="utf-8"))["elements"]
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in els if el["type"] == "node"}
    lon = np.array([v[0] for v in nodes.values()])
    lat = np.array([v[1] for v in nodes.values()])
    ex, ny = to_local(lon, lat, lat0, lon0)
    pos = dict(zip(nodes, zip(ex, ny)))
    segs = [(pos[u], pos[v]) for el in els if el["type"] == "way" for u, v in zip(el["nodes"], el["nodes"][1:])
            if u in pos and v in pos]
    s0, s1 = np.array([p for p, _ in segs]), np.array([q_ for _, q_ in segs])
    angle = grid_angle(s0[:, 0], s0[:, 1], s1[:, 0], s1[:, 1])
    pl = []
    for el in places["elements"]:
        pe, pn = to_local(el["lon"], el["lat"], lat0, lon0)
        if "name" in el.get("tags", {}):
            pl.append((float(pe), float(pn), el["tags"]["name"], PLACE_RANK.get(el["tags"].get("place"), 3)))
    zones = make_zones(ex, ny, pl, a.zone_km * 1000, a.min_nodes, short)
    if not 2 <= len(zones) <= 250:
        raise SystemExit(f"{len(zones)} zones: adjust --zone-km (the zone lookup holds 2 to 250)")
    print(f"{len(nodes):,} road nodes, {len(segs):,} segments; street grid {angle:+.1f} deg from north; {len(zones)} zones "
          f"({sum(1 for z in zones if not z['name'].startswith(short + ' '))} named after OpenStreetMap places)", flush=True)

    spec = dict(name=a.name, label=label, short=short, projection=f"local:{lat0:.6f},{lon0:.6f}", grid_angle=round(angle, 2),
                bbox=[round(v, 6) for v in box], zone_label="zones of about 1 km, named after OpenStreetMap places",
                data_note=f"no trip records for {short}: synthetic demand", demand_source="synthetic demand",
                tags={"surround": "land", "built_with": "orca.newcity", "built": time.strftime("%Y-%m-%d")})
    cityspec.CUSTOM.mkdir(parents=True, exist_ok=True)
    (cityspec.CUSTOM / f"{a.name}.json").write_text(json.dumps(spec, indent=2))
    (cityspec.CUSTOM / f"{a.name}_zones.json").write_text(json.dumps(dict(zones=zones)))

    py = sys.executable
    run([py, "scripts/build_streets.py", "--city", a.name], ROOT)
    if not a.skip_buildings:
        run([py, "scripts/build_buildings.py", "--city", a.name, "--download"], ROOT)
    try:
        run([py, "-m", "orca.chargers", "--city", a.name], ROOT)
    except subprocess.CalledProcessError:
        print("no charging stations fetched (the electric fleet will use evenly spread ones)")
    run([py, "frontend/build.py"], ROOT.parent)
    print(f"\n{label} is ready ({time.time() - t0:.0f}s):  python -m orca.server --city {a.name}")


if __name__ == "__main__":
    main()
