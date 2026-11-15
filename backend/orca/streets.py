"""Manhattan's real street network as ORCA's city (built by scripts/build_streets.py).

* Intersections and directed street segments from OpenStreetMap, with one-way rules and road
  classes; segments keep their full geometry, so cars follow the real curves of the streets.
* Travel time on a segment = length / (real median taxi speed for the hour x class factor x
  local congestion). The class factors (highways faster, side streets slower) are relative;
  the absolute speed comes from the TLC trip records (artifacts/nyc_traffic_profile.json).
* Shortest paths by free-flow travel time. Every request gets one reverse shortest-path tree
  (Dijkstra on the transposed graph, ~1 ms): it gives the road travel time from *every*
  candidate car to the rider and, followed forwards, each car's actual route. Trees are cached.
* Zones are the 63 official TLC taxi zones; ``zone_of`` uses a rasterised lookup (2-unit cells),
  with cells over water assigned to the nearest zone.
"""
from __future__ import annotations

import itertools
import json
import math
from collections import OrderedDict
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

UNREACHABLE = 1e9


class StreetCity:
    name = "manhattan"
    label = "Manhattan street network (OpenStreetMap)"
    RASTER = 2.0                 # zone lookup cell size, world units

    def __init__(self, d: dict):
        self.nx, self.nz = d["node_x"].astype(float), d["node_z"].astype(float)
        self.n = len(self.nx)
        self.src, self.dst = d["src"].astype(int), d["dst"].astype(int)
        self.length = d["length"].astype(float)
        self.cls = d["cls"].astype(int)
        self.class_speed = d["class_speed"].astype(float)
        self.class_major = d["class_major"].astype(int)
        self.edge_mult = self.class_speed[self.cls]
        self.edge_major = self.class_major[self.cls]
        self.geo_off, self.geo_x, self.geo_z = d["geo_off"], d["geo_x"].astype(float), d["geo_z"].astype(float)
        self.street_names_all = [str(s) for s in d["street_names"]]
        self.name_id = d["name_id"].astype(int)
        fr = d["frame"]
        self.frame = dict(e0=float(fr[0]), n0=float(fr[1]), angle=float(fr[2]), unit_m=float(fr[3]),
                          x_off=float(fr[4]), z_off=float(fr[5]))
        self.WX, self.WZ = float(fr[6]), float(fr[7])

        # graph: weight = free-flow time per unit speed (length / class multiplier)
        # Parallel segments between the same two intersections (a block and a curving road, say):
        # keep the fastest. A sparse matrix would otherwise *sum* duplicate entries.
        w = self.length / self.edge_mult
        self.edge_of: dict[tuple[int, int], int] = {}
        for i, (u, v) in enumerate(zip(self.src.tolist(), self.dst.tolist())):
            j = self.edge_of.get((u, v))
            if j is None or w[i] < w[j]:
                self.edge_of[(u, v)] = i
        best = np.fromiter(self.edge_of.values(), dtype=int)
        self.G = csr_matrix((w[best], (self.src[best], self.dst[best])), shape=(self.n, self.n))
        self.GT = self.G.T.tocsr()
        self.out_edges: list[list[int]] = [[] for _ in range(self.n)]
        for i, u in enumerate(self.src):
            self.out_edges[int(u)].append(i)
        self.kd = cKDTree(np.c_[self.nx, self.nz])
        self._trees: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

        # zones
        self.zone_ids = [int(z) for z in d["zone_ids"]]
        self.zone_names = [str(s) for s in d["zone_names"]]
        self.N_ZONES = len(self.zone_ids)
        rings = []
        for k in range(self.N_ZONES):
            rr = []
            for r in range(int(d["zone_ring_off"][k]), int(d["zone_ring_off"][k + 1])):
                a, b = int(d["ring_off"][r]), int(d["ring_off"][r + 1])
                rr.append(np.c_[d["ring_x"][a:b], d["ring_z"][a:b]])
            rings.append(rr)
        self.zone_rings = rings
        if "zone_lut" in d:
            self.zone_lut = d["zone_lut"].astype(np.int16)
            self.rh, self.rw = self.zone_lut.shape
        else:                                   # build time only (needs matplotlib)
            self._build_zone_raster()
        node_zone = self.zone_of_many(self.nx, self.nz)
        self.zone_nodes = [np.flatnonzero(node_zone == k) for k in range(self.N_ZONES)]
        self.zone_centers = [(float(self.nx[ns].mean()), float(self.nz[ns].mean())) if len(ns) else (0.0, 0.0)
                             for ns in self.zone_nodes]
        typical = math.sqrt(self.WX * self.WZ / self.N_ZONES)
        self._zone_scale = typical
        speed_file = Path(__file__).resolve().parents[1] / "artifacts" / "nyc_traffic_profile.json"
        try:
            prof = json.loads(speed_file.read_text())["speed_mps"]
            day = float(np.mean(prof[8:20]))
        except (OSError, ValueError, KeyError):
            day = 3.8
        self.base_speed = day / self.frame["unit_m"]          # world units per second at traffic factor 1
        self.speed_scale = 1.0
        cal = Path(__file__).resolve().parents[1] / "artifacts" / "speed_calibration_manhattan.json"
        if cal.exists():                                       # python -m orca.calibrate: fitted to real trip times
            self.speed_scale = float(json.loads(cal.read_text())["speed_scale"])
            self.base_speed /= self.speed_scale
        self.ambient_congestion = False                        # real hourly speeds already include congestion
        # Synthetic demand (tests only): a car here is busy ~13 min per ride (real-speed trips of ~2 km plus
        # the pickup), against ~6 min on the fast grid, so it can carry about half the grid's request rate.
        self.demand_scale = 0.5

    @classmethod
    def load(cls, path: Path) -> StreetCity:
        with np.load(path, allow_pickle=False) as z:
            return cls({k: z[k] for k in z.files})

    # ---- zones ---------------------------------------------------------------------------------
    def _build_zone_raster(self):
        from matplotlib.path import Path as MPath
        R = self.RASTER
        self.rw, self.rh = math.ceil(self.WX / R), math.ceil(self.WZ / R)
        cx, cz = np.meshgrid((np.arange(self.rw) + 0.5) * R, (np.arange(self.rh) + 0.5) * R)
        pts = np.c_[cx.ravel(), cz.ravel()]
        lut = np.full(len(pts), -1, dtype=np.int16)
        for k, rr in enumerate(self.zone_rings):
            verts, codes = [], []
            for r in rr:
                verts += [*r.tolist(), r[0].tolist()]
                codes += [MPath.MOVETO] + [MPath.LINETO] * (len(r) - 1) + [MPath.CLOSEPOLY]
            hit = MPath(verts, codes).contains_points(pts)
            lut[hit & (lut < 0)] = k
        inside = lut >= 0
        if (~inside).any():                     # water and gaps: nearest zone cell
            tree = cKDTree(pts[inside])
            _, j = tree.query(pts[~inside])
            lut[~inside] = lut[inside][j]
        self.zone_lut = lut.reshape(self.rh, self.rw)

    def zone_of(self, x: float, z: float) -> int:
        i = min(self.rw - 1, max(0, int(x / self.RASTER)))
        j = min(self.rh - 1, max(0, int(z / self.RASTER)))
        return int(self.zone_lut[j, i])

    def zone_of_many(self, x, z) -> np.ndarray:
        i = np.clip((np.asarray(x) / self.RASTER).astype(int), 0, self.rw - 1)
        j = np.clip((np.asarray(z) / self.RASTER).astype(int), 0, self.rh - 1)
        return self.zone_lut[j, i].astype(int)

    def zone_center(self, i: int) -> tuple[float, float]:
        return self.zone_centers[i]

    def zone_dist(self, a: int, b: int) -> float:
        (ax, az), (bx, bz) = self.zone_centers[a], self.zone_centers[b]
        return math.hypot(bx - ax, bz - az) / self._zone_scale

    # ---- points --------------------------------------------------------------------------------
    def nearest_node(self, x: float, z: float) -> int:
        return int(self.kd.query((x, z))[1])

    def snap(self, x: float, z: float) -> tuple[float, float]:
        k = self.nearest_node(x, z)
        return float(self.nx[k]), float(self.nz[k])

    def on_road(self, x: float, z: float) -> bool:
        return True

    def random_point(self, rng) -> tuple[float, float]:
        k = rng.randrange(self.n)
        return float(self.nx[k]), float(self.nz[k])

    def point_in_zone(self, zi: int, rng) -> tuple[float, float]:
        ns = self.zone_nodes[zi]
        k = int(ns[rng.randrange(len(ns))]) if len(ns) else rng.randrange(self.n)
        return float(self.nx[k]), float(self.nz[k])

    def street_name(self, x: float, z: float) -> str:
        k = self.nearest_node(x, z)
        for e in self.out_edges[k]:
            nm = self.street_names_all[self.name_id[e]]
            if nm:
                return nm
        return ""

    # ---- shortest paths --------------------------------------------------------------------------
    def tree_to(self, target: int) -> tuple[np.ndarray, np.ndarray]:
        """Free-flow time from every node to `target`, and each node's next hop toward it."""
        t = self._trees.get(target)
        if t is not None:
            self._trees.move_to_end(target)
            return t
        dist, pred = dijkstra(self.GT, indices=target, return_predecessors=True)
        t = (dist, pred)                      # on the transposed graph, pred[v] = next node from v toward target
        self._trees[target] = t
        if len(self._trees) > 512:
            self._trees.popitem(last=False)
        return t

    def _edge_points(self, e: int, mult_override: float | None = None) -> list[tuple]:
        a, b = int(self.geo_off[e]), int(self.geo_off[e + 1])
        m = float(self.edge_mult[e]) if mult_override is None else mult_override
        end = int(self.dst[e])
        return [(float(self.geo_x[k]), float(self.geo_z[k]), m, end if k == b - 1 else -1) for k in range(a + 1, b)]

    def _lead(self, agent) -> tuple[list[tuple], int]:
        """Waypoints from the car's position to the next intersection, and that intersection."""
        route, ri = getattr(agent, "route", None) or [], getattr(agent, "ri", 0)
        for k in range(ri, len(route)):
            if route[k][3] >= 0:
                return list(route[ri:k + 1]), int(route[k][3])
        node = getattr(agent, "node", -1)
        if node is None or node < 0:
            node = self.nearest_node(agent.x, agent.z)
        nx, nz = float(self.nx[node]), float(self.nz[node])
        lead = [] if (abs(nx - agent.x) < 1e-6 and abs(nz - agent.z) < 1e-6) else [(nx, nz, 1.0, node)]
        return lead, node

    def path_nodes(self, u: int, v: int) -> list[int]:
        if u == v:
            return [u]
        dist, nxt = self.tree_to(v)
        if not np.isfinite(dist[u]):
            return [u]
        out = [u]
        while out[-1] != v and len(out) < self.n:
            out.append(int(nxt[out[-1]]))
        return out

    def route(self, agent, tx: float, tz: float) -> list[tuple]:
        lead, start = self._lead(agent)
        target = self.nearest_node(tx, tz)
        out = lead
        nodes = self.path_nodes(start, target)
        for u, v in itertools.pairwise(nodes):
            out += self._edge_points(self.edge_of[(u, v)])
        return out

    def cruise(self, agent, rng) -> list[tuple]:
        """Idle cruising: drive a few blocks, turning at random onto outgoing streets (no U-turns if avoidable)."""
        lead, node = self._lead(agent)
        out, prev = lead, getattr(agent, "prev_node", -1)
        for _ in range(3):
            opts = [e for e in self.out_edges[node] if int(self.dst[e]) != prev] or self.out_edges[node]
            e = opts[rng.randrange(len(opts))]
            out += self._edge_points(e)
            prev, node = node, int(self.dst[e])
        return out

    def road_metrics(self, ax, az, bx, bz) -> tuple[float, float]:
        """(road distance along the fastest path, share of it on major roads) from point a to point b."""
        u, v = self.nearest_node(ax, az), self.nearest_node(bx, bz)
        if u == v:
            return math.hypot(bx - ax, bz - az), 0.0
        nodes = self.path_nodes(u, v)
        d = major = 0.0
        for p, q in itertools.pairwise(nodes):
            e = self.edge_of[(p, q)]
            d += self.length[e]
            major += self.length[e] * self.edge_major[e]
        lead = math.hypot(self.nx[u] - ax, self.nz[u] - az)
        return d + lead, (major / d if d > 0 else 0.0)

    def eta_many(self, cars: list[tuple[float, float, int]], bx: float, bz: float) -> np.ndarray:
        """Free-flow road time (in units / base speed) from each car (x, z, next node) to point b: one tree."""
        dist, _ = self.tree_to(self.nearest_node(bx, bz))
        out = np.empty(len(cars))
        for i, (x, z, node) in enumerate(cars):
            out[i] = dist[node] + math.hypot(self.nx[node] - x, self.nz[node] - z) if np.isfinite(dist[node]) else UNREACHABLE
        return out
