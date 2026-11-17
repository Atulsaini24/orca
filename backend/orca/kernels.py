"""Numba kernels for the engine's hot loops, all over flat arrays (no Python objects).

* ``step_fleet``: one simulated tick for every car: GPS pings (and the observed-speed feature they
  feed), random signal loss, driving along the route arena under local traffic, energy use, and the
  list of cars that reached the end of their route.
* ``build_grid`` / ``knn`` / ``knn_batch``: a bucket grid over car positions (counting sort) and
  exact k-nearest search in growing square rings.
* ``path_metrics``: road distance and major-road share from many start nodes along one
  shortest-path tree.
* ``build_route``: the waypoint array of a shortest path, copied from the street geometry.
* ``best_insertion``: the cheapest feasible place to insert a pickup and a drop-off into a car's
  stop sequence, under capacity, pickup and ride-time limits (shared rides).

All kernels are compiled once and cached on disk (``cache=True``).
"""
from __future__ import annotations

import math

import numpy as np
from numba import njit

S_IDLE, S_OFFERED, S_PICKUP, S_TRIP, S_CHARGE, S_PLUGGED, S_OFF = 0, 1, 2, 3, 4, 5, 6
ODO_CRUISE, ODO_REBAL, ODO_PICKUP, ODO_LOADED = 0, 1, 2, 3


@njit(cache=True, inline="always")
def _clampi(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


@njit(cache=True)
def step_fleet(dt, t, speed0, idle_cruise, ping_min, ping_jitter, off_rate,
               x, z, hx, hz, node, prev_node, state, rebal, load, offline_until, last_ping, next_ping, rx, rz,
               cruise_wait, r_pos, r_end, arena, odo, soc, use_per_unit, u,
               lut, raster, zone_factor, blobs, fs_speed, fs_n, fs_city, out_arrive, out_cruise):
    """Advance every car by dt seconds. Returns (cars that arrived, idle cars to send cruising, cars offline)."""
    n = x.shape[0]
    h, w = lut.shape
    nb = blobs.shape[0]
    na = nc = noff = 0
    p_off = off_rate * dt
    for i in range(n):
        if offline_until[i] < t and u[0, i] < p_off:          # random loss of signal for 2-7 minutes
            offline_until[i] = t + 120.0 + u[0, i] / p_off * 300.0
        online = offline_until[i] < t
        if not online:
            noff += 1
        s = state[i]
        if online and t >= next_ping[i]:
            if S_PICKUP <= s <= S_TRIP:                       # speeds are learned only from cars driving a job
                dtp = t - last_ping[i]
                if dtp > 0.5:
                    v = math.hypot(x[i] - rx[i], z[i] - rz[i]) / dtp
                    if v > 0.02:
                        zi = lut[_clampi(int((z[i] + rz[i]) * 0.5 / raster), 0, h - 1),
                                 _clampi(int((x[i] + rx[i]) * 0.5 / raster), 0, w - 1)]
                        fs_speed[zi] = 0.9 * fs_speed[zi] + 0.1 * v
                        fs_n[zi] += 1
                        fs_city[0] = 0.995 * fs_city[0] + 0.005 * v
            rx[i] = x[i]
            rz[i] = z[i]
            last_ping[i] = t
            next_ping[i] = t + ping_min + u[1, i] * ping_jitter
        if s == S_OFFERED:
            continue
        p, e = r_pos[i], r_end[i]
        if p < e:
            m = 1.0
            for b in range(nb):
                dx, dz, r = x[i] - blobs[b, 0], z[i] - blobs[b, 1], blobs[b, 2]
                m *= 1.0 - blobs[b, 3] * math.exp(-(dx * dx + dz * dz) / (2.0 * r * r))
            c = m * zone_factor[lut[_clampi(int(z[i] / raster), 0, h - 1), _clampi(int(x[i] / raster), 0, w - 1)]]
            if c < 0.22:
                c = 0.22
            cruising = s == S_IDLE and not rebal[i]
            rem = speed0 * c * dt * (idle_cruise if cruising else 1.0)
            moved = 0.0
            guard = 0
            while rem > 0.0 and p < e and guard < 64:
                guard += 1
                wx, wz, mult, nd = arena[p, 0], arena[p, 1], arena[p, 2], arena[p, 3]
                dx, dz = wx - x[i], wz - z[i]
                d = math.hypot(dx, dz)
                step = rem * mult
                if d > 1e-9:
                    hx[i], hz[i] = dx / d, dz / d
                if d <= step:
                    x[i], z[i] = wx, wz
                    rem -= d / mult
                    moved += d
                    p += 1
                    if nd >= 0.0:
                        prev_node[i] = node[i]
                        node[i] = int(nd)
                else:
                    x[i] += dx / d * step
                    z[i] += dz / d * step
                    moved += step
                    rem = 0.0
            r_pos[i] = p
            k = ODO_LOADED if load[i] > 0 else ODO_PICKUP if S_PICKUP <= s <= S_TRIP else ODO_CRUISE if cruising else ODO_REBAL
            odo[i, k] += moved
            soc[i] -= moved * use_per_unit
        if p >= e:
            if s == S_IDLE:
                rebal[i] = False
                cruise_wait[i] -= dt
                if cruise_wait[i] <= 0.0:
                    out_cruise[nc] = i
                    nc += 1
                    cruise_wait[i] = 10.0 + u[2, i] * 30.0
            elif s <= S_CHARGE:                          # not plugged in or off duty
                out_arrive[na] = i
                na += 1
    return na, nc, noff


# ---- spatial index -------------------------------------------------------------------------------
@njit(cache=True)
def build_grid(px, pz, mask, B, BN, cell_start, cell_items):
    """Counting sort of the cars where mask is true into BN x BN square buckets of side B."""
    cell_start[:] = 0
    n = px.shape[0]
    cell = np.empty(n, np.int64)
    for i in range(n):
        if mask[i]:
            c = _clampi(int(pz[i] // B), 0, BN - 1) * BN + _clampi(int(px[i] // B), 0, BN - 1)
            cell[i] = c
            cell_start[c + 1] += 1
        else:
            cell[i] = -1
    for c in range(BN * BN):
        cell_start[c + 1] += cell_start[c]
    fill = cell_start[:-1].copy()
    for i in range(n):
        c = cell[i]
        if c >= 0:
            cell_items[fill[c]] = i
            fill[c] += 1


@njit(cache=True)
def knn(x, z, k, excl, px, pz, state, want, B, BN, cell_start, cell_items, buf_d, buf_i, out):
    """Up to k cars nearest to (x, z) in straight line whose state is `want`, skipping ids in excl.

    Searches square rings of buckets outward and, once k are found, one ring more (a car in the next
    ring can still be closer than a corner of this one). Ties break on the lower car id. Returns the count.
    """
    cx, cz = _clampi(int(x // B), 0, BN - 1), _clampi(int(z // B), 0, BN - 1)
    nf, stop_at = 0, -1
    for r in range(BN):
        for bz in range(cz - r, cz + r + 1):
            if bz < 0 or bz >= BN:
                continue
            edge_row = bz == cz - r or bz == cz + r
            bx = cx - r
            while bx <= cx + r:
                if 0 <= bx < BN:
                    c = bz * BN + bx
                    for q in range(cell_start[c], cell_start[c + 1]):
                        i = cell_items[q]
                        if state[i] != want:
                            continue
                        skip = False
                        for e in excl:
                            if e == i:
                                skip = True
                                break
                        if skip:
                            continue
                        buf_d[nf] = (px[i] - x) ** 2 + (pz[i] - z) ** 2
                        buf_i[nf] = i
                        nf += 1
                bx += 1 if edge_row else 2 * r if r > 0 else 1
        if nf >= k:
            if stop_at < 0:
                stop_at = r + 1
            if r >= stop_at:
                break
    by_id = np.argsort(buf_i[:nf])
    order = by_id[np.argsort(buf_d[:nf][by_id], kind="mergesort")]      # by distance, ties by car id
    m = min(k, nf)
    for j in range(m):
        out[j] = buf_i[order[j]]
    return m


@njit(cache=True)
def knn_batch(qx, qz, k, excl_flat, excl_off, px, pz, state, want, B, BN, cell_start, cell_items, buf_d, buf_i, out, counts):
    for q in range(qx.shape[0]):
        counts[q] = knn(qx[q], qz[q], k, excl_flat[excl_off[q]:excl_off[q + 1]], px, pz, state, want,
                        B, BN, cell_start, cell_items, buf_d, buf_i, out[q])


# ---- street paths -----------------------------------------------------------------------------------
@njit(cache=True)
def _edge(u, v, adj_ptr, adj_dst, adj_edge):
    for q in range(adj_ptr[u], adj_ptr[u + 1]):
        if adj_dst[q] == v:
            return adj_edge[q]
    return -1


@njit(cache=True)
def path_metrics(starts, target, nxt, adj_ptr, adj_dst, adj_edge, length, major, out_d, out_major):
    """Road distance and length on major roads along the tree path from each start node to target."""
    n = nxt.shape[0]
    for s in range(starts.shape[0]):
        u, d, mj, steps = starts[s], 0.0, 0.0, 0
        while u != target and steps < n:
            v = nxt[u]
            if v < 0:
                d = np.inf
                break
            e = _edge(u, v, adj_ptr, adj_dst, adj_edge)
            d += length[e]
            mj += length[e] * major[e]
            u = v
            steps += 1
        out_d[s], out_major[s] = d, mj


@njit(cache=True)
def build_route(start, target, nxt, adj_ptr, adj_dst, adj_edge, geo_off, geo_x, geo_z, edge_mult, dst):
    """Waypoints (x, z, speed multiplier, node id at intersections else -1) along the tree path."""
    n = nxt.shape[0]
    count, u, steps = 0, start, 0
    while u != target and steps < n:
        v = nxt[u]
        if v < 0:
            break
        e = _edge(u, v, adj_ptr, adj_dst, adj_edge)
        count += geo_off[e + 1] - geo_off[e] - 1
        u = v
        steps += 1
    out = np.empty((count, 4))
    k, u, steps = 0, start, 0
    while u != target and steps < n:
        v = nxt[u]
        if v < 0:
            break
        e = _edge(u, v, adj_ptr, adj_dst, adj_edge)
        a, b = geo_off[e], geo_off[e + 1]
        for g in range(a + 1, b):
            out[k, 0], out[k, 1], out[k, 2] = geo_x[g], geo_z[g], edge_mult[e]
            out[k, 3] = dst[e] if g == b - 1 else -1.0
            k += 1
        u = v
        steps += 1
    return out


@njit(cache=True)
def cruise_route(node, prev, u, out_ptr, out_edge, dst, geo_off, geo_x, geo_z, edge_mult):
    """Idle cruising: len(u) blocks of random turns from node, avoiding U-turns where another street exists."""
    count, nd, pv = 0, node, prev
    picks = np.empty(u.shape[0], np.int64)
    for s in range(u.shape[0]):
        a, b = out_ptr[nd], out_ptr[nd + 1]
        if b == a:
            picks = picks[:s]
            break
        ok = 0
        for q in range(a, b):
            if dst[out_edge[q]] != pv:
                ok += 1
        k = int(u[s] * (ok if ok > 0 else b - a))
        e = -1
        for q in range(a, b):
            if ok == 0 or dst[out_edge[q]] != pv:
                if k == 0:
                    e = out_edge[q]
                    break
                k -= 1
        picks[s] = e
        count += geo_off[e + 1] - geo_off[e] - 1
        pv, nd = nd, dst[e]
    out = np.empty((count, 4))
    k = 0
    for e in picks:
        a, b = geo_off[e], geo_off[e + 1]
        for g in range(a + 1, b):
            out[k, 0], out[k, 1], out[k, 2] = geo_x[g], geo_z[g], edge_mult[e]
            out[k, 3] = dst[e] if g == b - 1 else -1.0
            k += 1
    return out


# ---- shared rides ------------------------------------------------------------------------------------
@njit(cache=True)
def best_insertion(T, now, lead, delta, deadline, load0, cap, new_pick_dl, new_max_ride, direct, w_delay):
    """Cheapest insertion of a new pickup p and drop-off d into a car's stop sequence.

    Points: 0 is the car, 1..m its current stops in order, m+1 the new pickup, m+2 the new drop-off;
    T[i, j] is the driving time from point i to point j, `lead` the time to reach point 0.
    delta[k] is +1 for a pickup stop, -1 for a drop-off; deadline[k] its latest allowed arrival.
    Inserting p after the first i stops and d after the first j >= i stops must keep every stop on
    time, the new rider's ride within new_max_ride, and the load within cap. Cost: the new rider's
    wait, plus w_delay x (how much later every rider already in the plan reaches their destination,
    plus the new rider's detour over a direct ride).
    Returns (cost, i, j, pickup time from now); cost is inf when no insertion is feasible.
    """
    m = delta.shape[0]
    P, D = m + 1, m + 2
    base_arr = np.empty(m)
    tt, prev = now + lead, 0
    for k in range(m):
        tt += T[prev, k + 1]
        base_arr[k] = tt
        prev = k + 1
    best, bi, bj, bpick = np.inf, -1, -1, 0.0
    seq = np.empty(m + 2, np.int64)
    for i in range(m + 1):
        for j in range(i, m + 1):
            q = 0
            for k in range(i):
                seq[q] = k + 1
                q += 1
            seq[q] = P
            q += 1
            for k in range(i, j):
                seq[q] = k + 1
                q += 1
            seq[q] = D
            q += 1
            for k in range(j, m):
                seq[q] = k + 1
                q += 1
            tt, prev, ld, ok = now + lead, 0, load0, True
            delay, t_pick, t_drop = 0.0, 0.0, 0.0
            for s in range(m + 2):
                pt = seq[s]
                tt += T[prev, pt]
                prev = pt
                if pt == P:
                    ld += 1
                    if tt > new_pick_dl:
                        ok = False
                        break
                    t_pick = tt
                elif pt == D:
                    ld -= 1
                    if tt - t_pick > new_max_ride:
                        ok = False
                        break
                    t_drop = tt
                else:
                    k = pt - 1
                    ld += delta[k]
                    if tt > deadline[k]:
                        ok = False
                        break
                    if delta[k] < 0:
                        delay += tt - base_arr[k]
                if ld > cap:
                    ok = False
                    break
            if not ok:
                continue
            cost = (t_pick - now) + w_delay * (delay + max(0.0, t_drop - t_pick - direct))
            if cost < best:
                best, bi, bj, bpick = cost, i, j, t_pick - now
    return best, bi, bj, bpick
