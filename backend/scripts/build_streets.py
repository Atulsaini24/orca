"""Build ORCA's routable Manhattan street network from OpenStreetMap and the TLC taxi zones.

    python scripts/build_streets.py --download        # fetch roads from the Overpass API, then build
    python scripts/build_streets.py                   # rebuild from data/osm/manhattan_roads.json

Steps
  1. Drivable OSM ways (motorway ... living_street, not private) in a box around Manhattan.
  2. Project to New York State Plane (EPSG:2263, metres) and keep only nodes inside the 63
     official Manhattan taxi-zone polygons: this drops New Jersey, Brooklyn, Queens, the Bronx
     and the small islands.
  3. Directed edges between consecutive way nodes, honouring oneway tags; contract chains of
     degree-2 nodes into single edges that keep their full geometry; keep the largest strongly
     connected component so every intersection can reach every other.
  4. Rotate by Manhattan's 29 degree street grid into ORCA world units (1 unit = 30 m).

Outputs
  artifacts/streets_manhattan.npz      network + zone polygons for the Python engine
  frontend/src/streets_data.js         the same, compacted, for the browser engine and renderer

Road data (c) OpenStreetMap contributors, ODbL. Taxi zones: NYC Taxi & Limousine Commission.
"""
from __future__ import annotations

import argparse
import base64
import itertools
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import prepare_tlc as P  # noqa: E402

from orca.geo import FT, UNIT_M, Frame, to_state_plane  # noqa: E402

OSM = ROOT / "data" / "osm" / "manhattan_roads.json"
CLASSES = {          # road class -> (speed multiplier vs. the real median taxi speed, major road?)
    "motorway": (2.2, 1), "trunk": (1.7, 1), "primary": (1.1, 1), "secondary": (1.0, 1), "tertiary": (0.95, 0),
    "unclassified": (0.85, 0), "residential": (0.85, 0), "living_street": (0.4, 0),
    "motorway_link": (1.3, 1), "trunk_link": (1.2, 1), "primary_link": (1.0, 1), "secondary_link": (0.95, 0), "tertiary_link": (0.9, 0),
}
CLASS_IDS = {k: i for i, k in enumerate(CLASSES)}
QUERY = ('[out:json][timeout:240];way["highway"~"^(' + "|".join(CLASSES) + ')$"]["access"!~"^(private|no)$"]["area"!="yes"]'
         '(40.698,-74.021,40.882,-73.906);(._;>;);out body qt;')


def download():
    OSM.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": "orca-dispatch-research/0.3"})
    with urllib.request.urlopen(req, timeout=300) as r:
        OSM.write_bytes(r.read())
    print(f"downloaded {OSM} ({OSM.stat().st_size / 1e6:.0f} MB)")


def zone_paths(shp):
    from matplotlib.path import Path as MPath
    zones = P.zone_polygons(Path(shp))
    out = {}
    for zid, (rings, name) in sorted(zones.items()):
        verts, codes = [], []
        for r in rings:
            r = r * FT
            verts += [*r.tolist(), r[0].tolist()]
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(r) - 1) + [MPath.CLOSEPOLY]
        out[zid] = (MPath(verts, codes), [r * FT for r in rings], name)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--shp", default=str(ROOT / "data/taxi_zones/taxi_zones/taxi_zones.shp"))
    a = ap.parse_args()
    if a.download or not OSM.exists():
        download()
    raw = json.loads(OSM.read_text(encoding="utf-8"))["elements"]
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in raw if e["type"] == "node"}
    ways = [e for e in raw if e["type"] == "way" and e.get("tags", {}).get("highway") in CLASSES]
    ids = np.fromiter(nodes, dtype=np.int64)
    lonlat = np.array([nodes[i] for i in ids])
    e, n = to_state_plane(lonlat[:, 0], lonlat[:, 1])
    zones = zone_paths(a.shp)
    pts = np.c_[e, n]
    zone_of_node = np.full(len(ids), -1)
    for zid, (path, _, _) in zones.items():
        hit = path.contains_points(pts)
        zone_of_node[hit & (zone_of_node < 0)] = zid
    keep = zone_of_node >= 0
    idx = {int(i): k for k, i in enumerate(ids)}
    print(f"{len(ways):,} OSM ways, {len(ids):,} nodes; {keep.sum():,} nodes inside Manhattan's taxi zones")

    # ---- directed segments between consecutive kept nodes
    seg = defaultdict(dict)              # u -> {v: (class, name)}
    for w in ways:
        t = w["tags"]
        cls = CLASS_IDS[t["highway"]]
        name = t.get("name", t.get("ref", ""))
        ow = t.get("oneway", "no")
        if t["highway"] == "motorway" and ow == "no":
            ow = "yes"
        nd = [idx[x] for x in w["nodes"] if x in idx]
        for u, v in itertools.pairwise(nd):
            if not (keep[u] and keep[v]) or u == v:
                continue
            if ow in ("yes", "true", "1", "no", "reversible", "false", "0"):
                seg[u][v] = (cls, name)
            if ow in ("-1",) or ow not in ("yes", "true", "1"):
                seg[v][u] = (cls, name)

    # ---- contract chains of pass-through nodes (one way in, one way out, same in both directions)
    indeg, outdeg = defaultdict(int), defaultdict(int)
    nbrs = defaultdict(set)
    for u, vs in seg.items():
        for v in vs:
            outdeg[u] += 1
            indeg[v] += 1
            nbrs[u].add(v)
            nbrs[v].add(u)
    allnodes = set(nbrs)
    def passthrough(x):
        return len(nbrs[x]) == 2 and indeg[x] == outdeg[x] and indeg[x] in (1, 2)
    is_key = {x: not passthrough(x) for x in allnodes}
    edges = []                           # (u_key, v_key, [node chain], class, name)
    for u in allnodes:
        if not is_key[u]:
            continue
        for v0, (cls, name) in seg[u].items():
            chain, prev, cur = [u], u, v0
            guard = 0
            while not is_key[cur] and guard < 10_000:
                chain.append(cur)
                nxt = [x for x in seg[cur] if x != prev]
                if not nxt:
                    break
                prev, cur = cur, nxt[0]
                guard += 1
            chain.append(cur)
            if is_key[cur]:
                edges.append((u, cur, chain, cls, name))

    # ---- largest strongly connected component
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    keys = sorted({x for ed in edges for x in (ed[0], ed[1])})
    kid = {x: i for i, x in enumerate(keys)}
    g = coo_matrix((np.ones(len(edges)), ([kid[ed[0]] for ed in edges], [kid[ed[1]] for ed in edges])), shape=(len(keys), len(keys)))
    _, lab = connected_components(g, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    keep_key = {x for x in keys if lab[kid[x]] == big}
    edges = [ed for ed in edges if ed[0] in keep_key and ed[1] in keep_key]
    keys = sorted(keep_key)
    kid = {x: i for i, x in enumerate(keys)}
    print(f"{len(keys):,} intersections and {len(edges):,} directed street segments in the largest strongly connected network")

    # ---- world frame: rotate by the street grid, 1 unit = 30 m, origin at the south-west corner + margin
    e_all, n_all = pts[:, 0][keep], pts[:, 1][keep]
    f0 = Frame(float(e_all.mean()), float(n_all.mean()))
    xk, zk = f0.to_world(pts[keys, 0], pts[keys, 1])
    margin = 12.0
    x_off, z_off = float(xk.min()) - margin, float(zk.min()) - margin
    def world(ii):
        x, z = f0.to_world(pts[ii, 0], pts[ii, 1])
        return x - x_off, z - z_off
    node_x, node_z = world(np.array(keys))
    WX, WZ = float(node_x.max() + margin), float(node_z.max() + margin)

    geo_off, geo_x, geo_z = [0], [], []
    src, dst, length, cls_a, name_id = [], [], [], [], []
    names: dict[str, int] = {"": 0}
    for u, v, chain, c, name in edges:
        gx, gz = world(np.array(chain))
        geo_x += gx.tolist()
        geo_z += gz.tolist()
        geo_off.append(len(geo_x))
        src.append(kid[u])
        dst.append(kid[v])
        length.append(float(np.hypot(np.diff(gx), np.diff(gz)).sum()))
        cls_a.append(c)
        name_id.append(names.setdefault(name, len(names)))

    # ---- zones in world units
    zids = sorted(zones)
    zpoly_off, zpoly_x, zpoly_z, ring_off = [0], [], [], [0]
    for zid in zids:
        for r in zones[zid][1]:
            x, z = f0.to_world(r[:, 0], r[:, 1])
            zpoly_x += (x - x_off).tolist()
            zpoly_z += (z - z_off).tolist()
            ring_off.append(len(zpoly_x))
        zpoly_off.append(len(ring_off) - 1)

    out = dict(node_x=node_x, node_z=node_z, src=np.array(src), dst=np.array(dst), length=np.array(length),
               cls=np.array(cls_a, dtype=np.int8), name_id=np.array(name_id), geo_off=np.array(geo_off), geo_x=np.array(geo_x),
               geo_z=np.array(geo_z), zone_ids=np.array(zids), zone_names=np.array([zones[z][2] for z in zids]),
               zone_ring_off=np.array(zpoly_off), ring_off=np.array(ring_off), ring_x=np.array(zpoly_x), ring_z=np.array(zpoly_z),
               street_names=np.array(list(names)), class_speed=np.array([v[0] for v in CLASSES.values()]),
               class_major=np.array([v[1] for v in CLASSES.values()]), class_names=np.array(list(CLASSES)),
               frame=np.array([f0.e0, f0.n0, f0.angle_deg, UNIT_M, x_off, z_off, WX, WZ]))
    art = ROOT / "artifacts" / "streets_manhattan.npz"
    from orca.streets import StreetCity
    out["zone_lut"] = StreetCity(out).zone_lut          # rasterised zone lookup, so runtime needs no GIS code
    np.savez_compressed(art, **out)
    print(f"world {WX:.0f} x {WZ:.0f} units ({WX * UNIT_M / 1000:.1f} x {WZ * UNIT_M / 1000:.1f} km); "
          f"{len(geo_x):,} geometry points; wrote {art.relative_to(ROOT)} ({art.stat().st_size / 1e6:.1f} MB)")

    # ---- browser copy: quantised to 0.05 units (1.5 m)
    q = lambda a_: np.round(np.asarray(a_) * 20).astype(np.uint16).tobytes()
    b64 = lambda b: base64.b64encode(b).decode()
    js = {
        "source": "Streets (c) OpenStreetMap contributors (ODbL); taxi zones: NYC TLC",
        "WX": WX, "WZ": WZ, "unit_m": UNIT_M, "n_nodes": len(keys), "n_edges": len(edges),
        "node_x": b64(q(node_x)), "node_z": b64(q(node_z)),
        "src": b64(np.array(src, dtype=np.uint16).tobytes()), "dst": b64(np.array(dst, dtype=np.uint16).tobytes()),
        "cls": b64(np.array(cls_a, dtype=np.uint8).tobytes()),
        "geo_off": b64(np.array(geo_off, dtype=np.uint32).tobytes()), "geo_x": b64(q(geo_x)), "geo_z": b64(q(geo_z)),
        "class_speed": [v[0] for v in CLASSES.values()], "class_major": [v[1] for v in CLASSES.values()],
        "zones": [dict(id=int(z), name=zones[z][2]) for z in zids],
        "zone_ring_off": [int(v) for v in zpoly_off], "ring_off": [int(v) for v in ring_off],
        "ring_x": b64(q(zpoly_x)), "ring_z": b64(q(zpoly_z)),
        "lut_w": int(out["zone_lut"].shape[1]), "lut_h": int(out["zone_lut"].shape[0]), "lut_cell": StreetCity.RASTER,
        "zone_lut": b64(out["zone_lut"].astype(np.uint8).tobytes()),
    }
    dest = ROOT.parent / "frontend" / "src" / "streets_data.js"
    dest.write_text("// Generated by backend/scripts/build_streets.py. Streets (c) OpenStreetMap contributors, ODbL. Do not edit.\n"
                    f"const STREETS_DATA = {json.dumps(js, separators=(',', ':'))};\n")
    print(f"wrote {dest.relative_to(ROOT.parent)} ({dest.stat().st_size / 1024:.0f} KB)")
    undirected = {(min(u, v), max(u, v), round(l, 1)) for u, v, l in zip(src, dst, length)}
    meta = dict(intersections=len(keys), segments=len(edges), zones=len(zids), street_km=round(sum(k[2] for k in undirected) * UNIT_M / 1000),
                one_way_share=round(1 - 2 * len({(min(u, v), max(u, v)) for u, v in zip(src, dst) if (v, u) in set(zip(src, dst))}) / max(1, len(edges)), 3),
                width_km=round(WX * UNIT_M / 1000, 1), length_km=round(WZ * UNIT_M / 1000, 1),
                source="OpenStreetMap contributors (ODbL), drivable ways; zones: NYC TLC taxi zones")
    (ROOT / "artifacts" / "city_manhattan.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote artifacts/city_manhattan.json: {meta}")


if __name__ == "__main__":
    main()
