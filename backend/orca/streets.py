"""Manhattan's real street network as ORCA's city (built by scripts/build_streets.py).

* Intersections and directed street segments from OpenStreetMap, with one-way rules and road
  classes; segments keep their full geometry, so cars follow the real curves of the streets.
* Travel time on a segment = length / (real median taxi speed for the hour x class factor x
  local congestion). The class factors (highways faster, side streets slower) are relative;
  the absolute speed comes from the city's trip records (artifacts/traffic_<city>.json): per zone and
  per hour, so Midtown at 6 pm is slower than Harlem at 6 pm.
* Shortest paths by free-flow travel time. Every request gets one reverse shortest-path tree
  (Dijkstra on the transposed graph, ~0.4 ms): it gives the road travel time from *every*
  candidate car to the rider and, followed forwards, each car's actual route. Trees are cached
  (LRU, float32). Walking a tree (routes, road distance) runs in compiled kernels (orca/kernels.py).
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

from . import kernels as K

UNREACHABLE = 1e9
TREE_CACHE = 1024
NO_POINTS = np.zeros((0, 4))


class StreetCity:
    RASTER = 2.0                 # zone lookup cell size, world units

    def __init__(self, d: dict):
        self.name = str(d["city"]) if "city" in d else "manhattan"
        short = str(d["label"]).split(",")[0] if "label" in d else "Manhattan"
        self.label = f"{short} street network (OpenStreetMap)"
        self.city_label = str(d["label"]) if "label" in d else "Manhattan, New York"
        self.zone_label = str(d["zone_label"]) if "zone_label" in d else "official TLC taxi zones"
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
        # the fastest edge for each (u, v), as CSR arrays the kernels can walk
        order = best[np.lexsort((self.dst[best], self.src[best]))]
        self.adj_ptr = np.cumsum(np.r_[0, np.bincount(self.src[order], minlength=self.n)]).astype(np.int64)
        self.adj_dst = self.dst[order].astype(np.int64)
        self.adj_edge = order.astype(np.int64)
        self.geo_off64 = np.asarray(self.geo_off, dtype=np.int64)
        self.dst_f = self.dst.astype(float)
        self.edge_major_f = self.edge_major.astype(float)
        out_order = np.argsort(self.src, kind="stable")             # every edge (parallel ones too), for cruising
        self.out_ptr = np.cumsum(np.r_[0, np.bincount(self.src, minlength=self.n)]).astype(np.int64)
        self.out_edge = out_order.astype(np.int64)
        self.dst64 = self.dst.astype(np.int64)
        self.kd = cKDTree(np.c_[self.nx, self.nz])
        # Riders are picked up on ordinary streets, never on an expressway: only intersections touching at
        # least one non-motorway street are valid pickup / drop-off points (cars still drive everything).
        motor = {i for i, n in enumerate(d["class_names"]) if str(n) in ("motorway", "motorway_link", "trunk_link")}             if "class_names" in d else {0, 8, 9}
        street = np.zeros(self.n, dtype=bool)
        ok_edge = ~np.isin(self.cls, list(motor))
        street[self.src[ok_edge]] = True
        street[self.dst[ok_edge]] = True
        self.pick_nodes = np.flatnonzero(street)
        self.kd_pick = cKDTree(np.c_[self.nx[self.pick_nodes], self.nz[self.pick_nodes]])
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
        pick = np.zeros(self.n, dtype=bool)
        pick[self.pick_nodes] = True
        self.zone_pick = [ns[pick[ns]] for ns in self.zone_nodes]
        self.zone_centers = [(float(self.nx[ns].mean()), float(self.nz[ns].mean())) if len(ns) else (0.0, 0.0)
                             for ns in self.zone_nodes]
        typical = math.sqrt(self.WX * self.WZ / self.N_ZONES)
        self._zone_scale = typical
        # Real traffic: hourly median speed for the city, and a zone x hour table of local speeds
        # (scripts/prepare_trips.py), both measured from this city's own trip records.
        art = Path(__file__).resolve().parents[1] / "artifacts"
        prof_file = art / f"traffic_{self.name}.json"
        if not prof_file.exists() and self.name == "manhattan":
            prof_file = art / "nyc_traffic_profile.json"             # older single-city profile
        self.zone_hour = None
        try:
            prof = json.loads(prof_file.read_text())
            day = float(np.mean(prof["speed_mps"][8:20]))
            self.hour_factor = np.clip(np.asarray(prof["factor"], dtype=float), 0.6, 1.8)
            if "zone_hour_factor" in prof and len(prof["zone_hour_factor"]) == self.N_ZONES:
                self.zone_hour = np.clip(np.asarray(prof["zone_hour_factor"], dtype=float), 0.4, 2.2)
        except (OSError, ValueError, KeyError):
            day, self.hour_factor = 3.8, np.ones(24)
        self.base_speed = day / self.frame["unit_m"]          # world units per second at traffic factor 1
        self.speed_scale = 1.0
        cal = art / f"speed_calibration_{self.name}.json"
        if cal.exists():                                       # python -m orca.calibrate: fitted to real trip times
            self.speed_scale = float(json.loads(cal.read_text())["speed_scale"])
            self.base_speed /= self.speed_scale
        self.ambient_congestion = False                        # real hourly speeds already include congestion
        # Synthetic demand (tests only): a car here is busy ~13 min per ride (real-speed trips of ~2 km plus
        # the pickup), against ~6 min on the fast grid, so it can carry about half the grid's request rate.
        self.demand_scale = 0.5

    def traffic(self, x: float, z: float, clock: float) -> float:
        """Local speed multiplier: the real speed of this zone at this hour relative to the city's daytime mean."""
        h = (clock / 3600) % 24
        i, f = int(h), h - int(h)
        if self.zone_hour is not None:
            row = self.zone_hour[self.zone_of(x, z)]
            return float(row[i] * (1 - f) + row[(i + 1) % 24] * f)
        return float(self.hour_factor[i] * (1 - f) + self.hour_factor[(i + 1) % 24] * f)

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

    def nearest_nodes(self, x, z) -> np.ndarray:
        return self.kd.query(np.c_[np.asarray(x, dtype=float), np.asarray(z, dtype=float)])[1].astype(np.int64)

    def nearest_pickup(self, x: float, z: float) -> int:
        return int(self.pick_nodes[self.kd_pick.query((x, z))[1]])

    def snap(self, x: float, z: float) -> tuple[float, float]:
        k = self.nearest_pickup(x, z)
        return float(self.nx[k]), float(self.nz[k])

    def on_road(self, x: float, z: float) -> bool:
        return True

    def random_point(self, rng) -> tuple[float, float]:
        k = int(self.pick_nodes[rng.randrange(len(self.pick_nodes))])
        return float(self.nx[k]), float(self.nz[k])

    def point_in_zone(self, zi: int, rng) -> tuple[float, float]:
        ns = self.zone_pick[zi]
        k = int(ns[rng.randrange(len(ns))]) if len(ns) else int(self.pick_nodes[rng.randrange(len(self.pick_nodes))])
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
        """Free-flow time (at unit speed) from every node to `target`, and each node's next hop toward it."""
        t = self._trees.get(target)
        if t is not None:
            self._trees.move_to_end(target)
            return t
        dist, pred = dijkstra(self.GT, indices=target, return_predecessors=True)
        # on the transposed graph, pred[v] = next node from v toward target (negative when none)
        t = (dist.astype(np.float32), pred.astype(np.int64))
        self._trees[target] = t
        if len(self._trees) > TREE_CACHE:
            self._trees.popitem(last=False)
        return t

    def _edge_points(self, e: int, mult_override: float | None = None) -> list[tuple]:
        a, b = int(self.geo_off[e]), int(self.geo_off[e + 1])
        m = float(self.edge_mult[e]) if mult_override is None else mult_override
        end = int(self.dst[e])
        return [(float(self.geo_x[k]), float(self.geo_z[k]), m, end if k == b - 1 else -1) for k in range(a + 1, b)]

    def _lead(self, agent) -> tuple[np.ndarray, int]:
        """Waypoints from the car's position to the next intersection, and that intersection."""
        route = getattr(agent, "route", None)
        ri = getattr(agent, "ri", 0)
        if route is not None and len(route) > ri:
            r = np.asarray(route, dtype=float).reshape(-1, 4)
            hit = np.flatnonzero(r[ri:, 3] >= 0)
            if len(hit):
                k = ri + int(hit[0])
                return r[ri:k + 1], int(r[k, 3])
        node = getattr(agent, "node", -1)
        if node is None or node < 0:
            node = self.nearest_node(agent.x, agent.z)
        nx, nz = float(self.nx[node]), float(self.nz[node])
        lead = NO_POINTS if (abs(nx - agent.x) < 1e-6 and abs(nz - agent.z) < 1e-6) else np.array([[nx, nz, 1.0, node]])
        return lead, int(node)

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

    def route(self, agent, tx: float, tz: float) -> np.ndarray:
        """Waypoints (x, z, speed multiplier, node or -1) from the car along the fastest path to (tx, tz)."""
        lead, start = self._lead(agent)
        target = self.nearest_node(tx, tz)
        if start == target:
            return lead
        _, nxt = self.tree_to(target)
        path = K.build_route(start, target, nxt, self.adj_ptr, self.adj_dst, self.adj_edge, self.geo_off64,
                             self.geo_x, self.geo_z, self.edge_mult, self.dst_f)
        return np.concatenate([lead, path]) if len(lead) else path

    def cruise(self, agent, rng) -> np.ndarray:
        """Idle cruising: drive a few blocks, turning at random onto outgoing streets (no U-turns if avoidable)."""
        lead, node = self._lead(agent)
        # no U-turn at the next intersection: mid-street, we came from the last node passed
        prev = getattr(agent, "node", -1) if len(lead) else getattr(agent, "prev_node", -1)
        u = np.array([rng.random(), rng.random(), rng.random()])
        path = K.cruise_route(node, prev, u, self.out_ptr, self.out_edge, self.dst64, self.geo_off64, self.geo_x,
                              self.geo_z, self.edge_mult)
        return np.concatenate([lead, path]) if len(lead) else path

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

    def road_metrics_many(self, ax, az, bx, bz) -> tuple[np.ndarray, np.ndarray]:
        """road_metrics for many pairs at once: one tree per distinct destination, walked in a kernel."""
        ax, az, bx, bz = (np.asarray(v, dtype=float) for v in (ax, az, bx, bz))
        u, v = self.nearest_nodes(ax, az), self.nearest_nodes(bx, bz)
        d, major = np.empty(len(ax)), np.empty(len(ax))
        for tgt in np.unique(v):
            sel = np.flatnonzero(v == tgt)
            _, nxt = self.tree_to(int(tgt))
            dd, mm = np.empty(len(sel)), np.empty(len(sel))
            K.path_metrics(u[sel], int(tgt), nxt, self.adj_ptr, self.adj_dst, self.adj_edge, self.length,
                           self.edge_major_f, dd, mm)
            d[sel], major[sel] = dd, mm
        same = u == v
        share = np.divide(major, d, out=np.zeros_like(d), where=d > 0)
        lead = np.hypot(self.nx[u] - ax, self.nz[u] - az)
        return np.where(same, np.hypot(bx - ax, bz - az), d + lead), np.where(same, 0.0, share)

    def ff_matrix(self, fx, fz, tx, tz) -> np.ndarray:
        """Free-flow driving time at unit speed from each point in f to each point in t (one tree per t)."""
        fu, tv = self.nearest_nodes(fx, fz), self.nearest_nodes(tx, tz)
        M = np.empty((len(fu), len(tv)))
        for j, v in enumerate(tv.tolist()):
            M[:, j] = self.tree_to(v)[0][fu]
        return M

    def city_factor(self, clock: float) -> float:
        """Citywide speed multiplier for the hour (real median trip speed relative to the daytime mean)."""
        h = (clock / 3600) % 24
        i, f = int(h), h - int(h)
        return float(self.hour_factor[i] * (1 - f) + self.hour_factor[(i + 1) % 24] * f)

    def zone_factors(self, clock: float) -> np.ndarray:
        """Local speed multiplier of every zone at this time of day (real zone x hour speeds)."""
        h = (clock / 3600) % 24
        i, f = int(h), h - int(h)
        if self.zone_hour is not None:
            return self.zone_hour[:, i] * (1 - f) + self.zone_hour[:, (i + 1) % 24] * f
        return np.full(self.N_ZONES, self.city_factor(clock))

    def eta_many(self, cars: list[tuple[float, float, int]], bx: float, bz: float) -> np.ndarray:
        """Free-flow road time (in units / base speed) from each car (x, z, next node) to point b: one tree."""
        dist, _ = self.tree_to(self.nearest_node(bx, bz))
        out = np.empty(len(cars))
        for i, (x, z, node) in enumerate(cars):
            out[i] = dist[node] + math.hypot(self.nx[node] - x, self.nz[node] - z) if np.isfinite(dist[node]) else UNREACHABLE
        return out
