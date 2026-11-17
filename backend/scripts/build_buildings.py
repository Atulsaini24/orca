"""Turn a city's official building footprints into ORCA's 3D skyline: real outlines at real heights.

    python scripts/build_buildings.py --city manhattan [--download]
    python scripts/build_buildings.py --city chicago        # data from scripts/download_chicago.py

Sources
  manhattan  NYC Open Data "BUILDING" (5zhs-2jue), DoITT: every footprint with its surveyed roof height
  chicago    City of Chicago "Building Footprints" (syp8-uezg): footprints with storey counts
             (height = storeys x 3.6 m; 3 storeys where unknown)

Each footprint is projected into the city's street-network frame (the same transform as its streets
and trips), simplified with Douglas-Peucker to ~3 m, and kept only if it lies inside the city's zones.
The renderer extrudes the real outline; buildings whose outline is too complex fall back to their
minimum-area rectangle.

Output: frontend/data/<city>/buildings.js
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull, QhullError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from orca import cityspec  # noqa: E402
from orca.geo import Frame  # noqa: E402

FT_M = 0.3048
MAX_PTS = 32                      # outline vertices kept per building (more: rectangle fallback)
SIMPLIFY = 0.1                    # Douglas-Peucker tolerance in world units (3 m)
SOURCES = {
    "manhattan": dict(path=ROOT / "data" / "nyc_buildings_manhattan.json",
                      api="https://data.cityofnewyork.us/resource/5zhs-2jue.json",
                      query={"$select": "name,height_roof,construction_year,the_geom", "$where": "starts_with(base_bbl,'1')", "$limit": "60000"},
                      label="NYC Open Data, BUILDING footprints (5zhs-2jue), NYC DoITT"),
    "chicago": dict(path=ROOT / "data" / "chicago" / "buildings.json", api=None, query=None,
                    label="City of Chicago, Building Footprints (syp8-uezg)"),
}


def osm_source(name: str) -> dict:
    """Any other city: building footprints from OpenStreetMap, height from height or building:levels tags."""
    return dict(path=ROOT / "data" / "osm" / f"{name}_buildings.json", api="overpass", query=None,
                label="OpenStreetMap contributors (ODbL), building footprints")


def download_osm(spec, src):
    """Overpass: every building way in the city's box, with geometry; written in the same row format as the others."""
    from orca.osm import bbox_filter, query
    data = query(f'[out:json][timeout:600][maxsize:1073741824];way["building"]{bbox_filter(spec.bbox)};out tags geom;', timeout=900)
    rows = []
    for el in data["elements"]:
        g = el.get("geometry")
        if not g or len(g) < 4:
            continue
        t = el.get("tags", {})
        h = None
        for k in ("height", "building:height"):
            try:
                h = float(str(t.get(k, "")).replace("m", "").strip())
                break
            except ValueError:
                pass
        if h is None:
            try:
                h = float(t["building:levels"]) * 3.2 + 1.5
            except (KeyError, ValueError):
                h = None
        rows.append(dict(the_geom=dict(type="Polygon", coordinates=[[[p["lon"], p["lat"]] for p in g]]), height_m=h,
                         year=t.get("start_date", "")[:4]))
    src["path"].parent.mkdir(parents=True, exist_ok=True)
    src["path"].write_text(json.dumps(rows))
    print(f"downloaded {len(rows):,} building footprints from OpenStreetMap")


def download(src):
    with urllib.request.urlopen(f"{src['api']}?{urllib.parse.urlencode(src['query'])}", timeout=900) as r:
        src["path"].write_bytes(r.read())
    print(f"downloaded {src['path']} ({src['path'].stat().st_size / 1e6:.0f} MB)")


def height_m(city: str, row: dict) -> float:
    if "height_m" in row:                                   # OpenStreetMap: tagged height, else about three storeys
        h = row["height_m"]
        return float(h) if h and 2 < h < 600 else 9.0
    if city == "manhattan":
        h = float(row.get("height_roof") or 0) * FT_M
        return h if h > 0 else 10.0
    st = float(row.get("stories") or 0)
    return st * 3.6 if st > 0 else 10.8


def year(row: dict) -> int:
    try:
        return int(float(row.get("construction_year") or row.get("year_built") or row.get("year") or 0))
    except ValueError:
        return 0


def dp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker simplification of an open polyline."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    L = np.hypot(*ab)
    rel = pts - a
    d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / L if L > 1e-12 else np.hypot(*rel.T)   # 2-D cross product
    i = int(np.argmax(d))
    if d[i] <= tol:
        return np.vstack([a, b])
    return np.vstack([dp(pts[: i + 1], tol)[:-1], dp(pts[i:], tol)])


def simplify_ring(ring: np.ndarray, tol: float) -> np.ndarray:
    if np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    if len(ring) < 3:
        return ring
    k = int(np.argmax(np.hypot(*(ring - ring[0]).T)))           # split at the farthest point, simplify both halves
    out = np.vstack([dp(ring[: k + 1], tol)[:-1], dp(np.vstack([ring[k:], ring[:1]]), tol)[:-1]])
    return out


def min_area_rect(pts: np.ndarray) -> np.ndarray:
    try:
        hull = pts[ConvexHull(pts).vertices]
    except (QhullError, ValueError):
        hull = pts
    edges = np.diff(np.vstack([hull, hull[:1]]), axis=0)
    best = None
    for a in np.unique(np.mod(np.arctan2(edges[:, 1], edges[:, 0]), np.pi / 2)):
        c, s = np.cos(a), np.sin(a)
        u, v = hull @ np.array([c, s]), hull @ np.array([-s, c])
        area = np.ptp(u) * np.ptp(v)
        if best is None or area < best[0]:
            best = (area, a, u.min(), u.max(), v.min(), v.max())
    _, a, u0, u1, v0, v1 = best
    c, s = np.cos(a), np.sin(a)
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return np.array([(u * c - v * s, u * s + v * c) for u, v in corners])


def area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="manhattan", choices=list(cityspec.CITIES))
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    spec = cityspec.get(a.city)
    src = SOURCES.get(a.city) or osm_source(a.city)
    if a.download or not src["path"].exists():
        if not src["api"]:
            sys.exit(f"{src['path']} missing: run scripts/download_chicago.py --only buildings")
        download_osm(spec, src) if src["api"] == "overpass" else download(src)
    d = np.load(spec.streets)
    f = d["frame"]
    frame, x_off, z_off, WX, WZ, unit = (Frame(float(f[0]), float(f[1]), float(f[2]), float(f[3])),
                                         float(f[4]), float(f[5]), float(f[6]), float(f[7]), float(f[3]))
    rows = json.loads(src["path"].read_text(encoding="utf-8"))
    rings, heights, years, kinds, skipped = [], [], [], [], 0
    for r in rows:
        g = r.get("the_geom") or {}
        polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])] if g.get("type") == "Polygon" else []
        hm = height_m(a.city, r)
        for poly in polys:
            if not poly or len(poly[0]) < 4:
                continue
            ll = np.asarray(poly[0], dtype=float)
            e, n = spec.project(ll[:, 0], ll[:, 1])
            x, z = frame.to_world(e, n)
            pts = np.c_[x - x_off, z - z_off]
            if area(pts) * unit * unit < 25:                    # sheds and kiosks
                skipped += 1
                continue
            cx, cz = pts.mean(0)
            if not (0 <= cx < WX and 0 <= cz < WZ):
                skipped += 1
                continue
            if not _inside_zones(d, cx, cz):                    # outside the simulated area
                skipped += 1
                continue
            s = simplify_ring(pts, SIMPLIFY)
            if len(s) < 3 or len(s) > MAX_PTS or area(s) < 0.5 * area(pts):
                s, kind = min_area_rect(pts), 1                 # rectangle fallback
            else:
                kind = 0
            rings.append(s)
            heights.append(hm / unit)
            years.append(year(r))
            kinds.append(kind)
    n = len(rings)
    counts = np.array([len(x) for x in rings], dtype=np.uint8)
    xy = np.vstack(rings)
    hs = np.array(heights)
    print(f"{spec.short}: {len(rows):,} footprints -> {n:,} buildings ({skipped:,} tiny or outside skipped); "
          f"{np.mean(np.array(kinds) == 0) * 100:.0f}% real outlines, {counts.mean():.1f} vertices each; "
          f"tallest {hs.max() * unit:.0f} m, median {np.median(hs) * unit:.0f} m")
    q = lambda v, s_: np.clip(np.round(np.asarray(v) * s_), 0, 65535).astype(np.uint16).tobytes()
    b64 = lambda b: base64.b64encode(b).decode()
    js = dict(source=src["label"], city=spec.name, n=n, unit_m=unit, counts=b64(counts.tobytes()),
              x=b64(q(xy[:, 0], 20)), z=b64(q(xy[:, 1], 20)), h=b64(q(hs, 1000)),
              year=b64(np.clip(np.array(years), 0, 65535).astype(np.uint16).tobytes()))
    dest = ROOT.parent / "frontend" / "data" / spec.name / "buildings.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("// Generated by backend/scripts/build_buildings.py from official building footprints. Do not edit.\n"
                    f"var BUILDINGS_DATA = {json.dumps(js, separators=(',', ':'))};\n")
    print(f"wrote {dest.relative_to(ROOT.parent)} ({dest.stat().st_size / 1024:.0f} KB)")
    meta = json.loads(spec.meta.read_text()) if spec.meta.exists() else {}
    meta.update(buildings=n, footprints=len(rows), tallest_m=round(float(hs.max() * unit)), median_height_m=round(float(np.median(hs) * unit)),
                buildings_over_150m=int((hs * unit > 150).sum()), real_outline_share=round(float(np.mean(np.array(kinds) == 0)), 3),
                buildings_source=src["label"])
    spec.meta.write_text(json.dumps(meta, indent=2))


_ZPATH = {}


def _inside_zones(d, x, z) -> bool:
    """Point-in-zone test against the city's zone polygons (cached matplotlib paths)."""
    from matplotlib.path import Path as MPath
    key = id(d)
    if key not in _ZPATH:
        verts, codes = [], []
        for r in range(len(d["ring_off"]) - 1):
            a, b = int(d["ring_off"][r]), int(d["ring_off"][r + 1])
            ring = np.c_[d["ring_x"][a:b], d["ring_z"][a:b]]
            verts += [*ring.tolist(), ring[0].tolist()]
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(ring) - 1) + [MPath.CLOSEPOLY]
        _ZPATH[key] = MPath(verts, codes)
    return bool(_ZPATH[key].contains_point((x, z)))


if __name__ == "__main__":
    main()
