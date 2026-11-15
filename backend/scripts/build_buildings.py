"""Turn NYC's official building footprints into ORCA's 3D skyline.

    python scripts/build_buildings.py --download      # fetch from NYC Open Data, then build
    python scripts/build_buildings.py                 # rebuild from data/nyc_buildings_manhattan.json

Source: NYC Open Data "BUILDING" (dataset 5zhs-2jue), Department of Information Technology and
Telecommunications: every Manhattan footprint (borough code 1) with its surveyed roof height.

Each footprint is projected into the street network's world frame (the same transform as the
streets and the taxi trips) and approximated by its minimum-area bounding rectangle, found with
rotating calipers over the convex hull. Rectangles that cover an irregular footprint poorly are
shrunk to the footprint's area so they do not spill into streets. The renderer draws them as
instanced boxes at their real heights.

Output: frontend/src/buildings_data.js
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
from orca.geo import Frame, to_state_plane  # noqa: E402

SRC = ROOT / "data" / "nyc_buildings_manhattan.json"
FT_M = 0.3048
API = "https://data.cityofnewyork.us/resource/5zhs-2jue.json"


def download():
    q = urllib.parse.urlencode({"$select": "name,height_roof,construction_year,the_geom",
                                "$where": "starts_with(base_bbl,'1')", "$limit": "60000"})
    with urllib.request.urlopen(f"{API}?{q}", timeout=900) as r:
        SRC.write_bytes(r.read())
    print(f"downloaded {SRC} ({SRC.stat().st_size / 1e6:.0f} MB)")


def min_area_rect(pts: np.ndarray):
    """(cx, cz, width along angle, depth, angle, rect area) of the minimum-area enclosing rectangle."""
    try:
        hull = pts[ConvexHull(pts).vertices]
    except (QhullError, ValueError):
        hull = pts
    edges = np.diff(np.vstack([hull, hull[:1]]), axis=0)
    angles = np.unique(np.mod(np.arctan2(edges[:, 1], edges[:, 0]), np.pi / 2))
    best = None
    for a in angles:
        c, s = np.cos(a), np.sin(a)
        u = hull[:, 0] * c + hull[:, 1] * s
        v = -hull[:, 0] * s + hull[:, 1] * c
        area = (u.max() - u.min()) * (v.max() - v.min())
        if best is None or area < best[0]:
            best = (area, a, u.min(), u.max(), v.min(), v.max())
    area, a, u0, u1, v0, v1 = best
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    c, s = np.cos(a), np.sin(a)
    return cu * c - cv * s, cu * s + cv * c, u1 - u0, v1 - v0, a, area


def poly_area(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    if a.download or not SRC.exists():
        download()
    d = np.load(ROOT / "artifacts" / "streets_manhattan.npz")
    f = d["frame"]
    frame, x_off, z_off, WX, WZ = Frame(float(f[0]), float(f[1]), float(f[2]), float(f[3])), float(f[4]), float(f[5]), float(f[6]), float(f[7])
    unit = float(f[3])
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    out, skipped = [], 0
    for r in rows:
        g = r.get("the_geom") or {}
        polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])] if g.get("type") == "Polygon" else []
        h_m = float(r.get("height_roof") or 0) * FT_M
        if h_m <= 0:
            h_m = 10.0                                   # unknown: a typical three-storey building
        for poly in polys:
            if not poly or len(poly[0]) < 4:
                continue
            ring = np.asarray(poly[0], dtype=float)
            e, n = to_state_plane(ring[:, 0], ring[:, 1])
            x, z = frame.to_world(e, n)
            pts = np.c_[x - x_off, z - z_off]
            fa = poly_area(pts)
            if fa * unit * unit < 25:                    # sheds and kiosks under 25 m2
                skipped += 1
                continue
            cx, cz, w, dd, ang, ra = min_area_rect(pts)
            fill = fa / max(ra, 1e-9)
            if fill < 0.75:                              # irregular footprint: shrink to its true area
                k = np.sqrt(fill / 0.75)
                w, dd = w * k, dd * k
            if not (0 <= cx <= WX and 0 <= cz <= WZ):
                skipped += 1
                continue
            out.append((cx, cz, w, dd, ang, h_m / unit, int(r.get("construction_year") or 0)))
    arr = np.array(out)
    print(f"{len(rows):,} footprints -> {len(arr):,} building blocks ({skipped:,} tiny or outside skipped); "
          f"tallest {arr[:, 5].max() * unit:.0f} m; median height {np.median(arr[:, 5]) * unit:.0f} m")
    q = lambda v, s: np.clip(np.round(np.asarray(v) * s), 0, 65535).astype(np.uint16).tobytes()
    b64 = lambda b: base64.b64encode(b).decode()
    js = dict(source="NYC Open Data, BUILDING footprints (5zhs-2jue), NYC DoITT", n=len(arr), unit_m=unit,
              x=b64(q(arr[:, 0], 50)), z=b64(q(arr[:, 1], 50)), w=b64(q(arr[:, 2], 200)), d=b64(q(arr[:, 3], 200)),
              a=b64(np.round(np.mod(arr[:, 4], np.pi) / np.pi * 255).astype(np.uint8).tobytes()),
              h=b64(q(arr[:, 5], 1000)), year=b64(np.clip(arr[:, 6], 0, 65535).astype(np.uint16).tobytes()))
    dest = ROOT.parent / "frontend" / "src" / "buildings_data.js"
    dest.write_text("// Generated by backend/scripts/build_buildings.py from NYC Open Data building footprints. Do not edit.\n"
                    f"const BUILDINGS_DATA = {json.dumps(js, separators=(',', ':'))};\n")
    print(f"wrote {dest.relative_to(ROOT.parent)} ({dest.stat().st_size / 1024:.0f} KB)")
    meta_p = ROOT / "artifacts" / "city_manhattan.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    meta.update(buildings=len(arr), footprints=len(rows), tallest_m=round(float(arr[:, 5].max() * unit)),
                median_height_m=round(float(np.median(arr[:, 5]) * unit)), buildings_over_150m=int((arr[:, 5] * unit > 150).sum()),
                buildings_source="NYC Open Data, BUILDING footprints (5zhs-2jue)")
    meta_p.write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
