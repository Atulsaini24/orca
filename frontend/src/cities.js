// =============================================================================
// Cities for the browser engine: the same API as backend/orca/city.py and streets.py.
//   StreetCity  Manhattan's real streets (OpenStreetMap) and the 63 TLC taxi zones
//   GridCity    the synthetic 28 x 28 grid (tests, or ?city=grid)
// Routes are flat arrays with stride 4: x, z, speed multiplier of the leg, node id (or -1).
// =============================================================================
function b64Bytes(s) {
  const bin = typeof atob === 'function' ? atob(s) : Buffer.from(s, 'base64').toString('binary');
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
const b64U16 = (s) => { const b = b64Bytes(s); return new Uint16Array(b.buffer, b.byteOffset, b.byteLength >> 1); };
const b64U32 = (s) => { const b = b64Bytes(s); return new Uint32Array(b.buffer, b.byteOffset, b.byteLength >> 2); };

class GridCity {
  constructor() {
    this.name = 'grid'; this.label = 'synthetic grid';
    this.WX = this.WZ = CFG.W; this.N = CFG.ZONES * CFG.ZONES;
    this.baseSpeed = CFG.BASE_SPEED; this.ambientCongestion = true; this.demandScale = 1;
  }
  zoneOf(x, z) { return zoneOfGrid(x, z); }
  zoneName(i) { return String.fromCharCode(65 + (i % CFG.ZONES)) + (1 + Math.floor(i / CFG.ZONES)); }
  zoneCenter(i) { return [((i % CFG.ZONES) + 0.5) * CFG.ZSIZE, (Math.floor(i / CFG.ZONES) + 0.5) * CFG.ZSIZE]; }
  zoneDist(a, b) { const Z = CFG.ZONES; return Math.hypot((a % Z) - (b % Z), Math.floor(a / Z) - Math.floor(b / Z)); }
  snap(x, z) { return [snap(x), snap(z)]; }
  randomPoint(r) { return r() < 0.5 ? [r() * CFG.W, Math.floor(r() * CFG.GRID) * CFG.CELL] : [Math.floor(r() * CFG.GRID) * CFG.CELL, r() * CFG.W]; }
  pointInZone(zi, r) { const zx = zi % CFG.ZONES, zz = Math.floor(zi / CFG.ZONES); return [snap((zx + 0.2 + 0.6 * r()) * CFG.ZSIZE), snap((zz + 0.2 + 0.6 * r()) * CFG.ZSIZE)]; }
  route(a, tx, tz) {
    const C = CFG.CELL, pts = [];
    let x = a.x, z = a.z;
    const onV = x % C === 0, onH = z % C === 0;
    if (!(onV && onH)) {
      if (onH) { const cx = tx > x ? Math.ceil(x / C) * C : Math.floor(x / C) * C; pts.push([cx, z]); x = cx; }
      else { const cz = tz > z ? Math.ceil(z / C) * C : Math.floor(z / C) * C; pts.push([x, cz]); z = cz; }
    }
    const xFirst = isArterial(z / C) || (!isArterial(x / C) && (a.id + (tx + tz) / C) % 2 === 0);
    if (xFirst) { if (x !== tx) pts.push([tx, z]); if (z !== tz) pts.push([tx, tz]); }
    else { if (z !== tz) pts.push([x, tz]); if (x !== tx) pts.push([tx, tz]); }
    const out = []; let px = a.x, pz = a.z;
    for (const [wx, wz] of pts) {
      const line = wx !== px ? pz / C : px / C;
      out.push(wx, wz, Number.isInteger(line) && isArterial(line) ? CFG.ART_MULT : 1, -1);
      px = wx; pz = wz;
    }
    return out;
  }
  cruise(a, r) {
    const C = CFG.CELL;
    return this.route(a, snap(a.x + Math.round((r() - 0.5) * 4) * C), snap(a.z + Math.round((r() - 0.5) * 4) * C));
  }
  roadDist(ax, az, bx, bz) { return Math.abs(bx - ax) + Math.abs(bz - az); }
}

class StreetCity {
  constructor(D) {
    this.name = 'manhattan'; this.label = 'Manhattan street network (OpenStreetMap)';
    this.WX = D.WX; this.WZ = D.WZ; this.source = D.source;
    const q = (s) => Float32Array.from(b64U16(s), (v) => v / 20);
    this.nx = q(D.node_x); this.nz = q(D.node_z); this.n = this.nx.length;
    this.src = b64U16(D.src); this.dst = b64U16(D.dst); this.cls = b64Bytes(D.cls);
    this.geoOff = b64U32(D.geo_off); this.gx = q(D.geo_x); this.gz = q(D.geo_z);
    this.classSpeed = D.class_speed; this.classMajor = D.class_major;
    const E = this.src.length;
    this.mult = new Float32Array(E); this.len = new Float32Array(E);
    for (let e = 0; e < E; e++) {
      this.mult[e] = this.classSpeed[this.cls[e]];
      let L = 0;
      for (let k = this.geoOff[e] + 1; k < this.geoOff[e + 1]; k++) L += Math.hypot(this.gx[k] - this.gx[k - 1], this.gz[k] - this.gz[k - 1]);
      this.len[e] = L;
    }
    // fastest edge per (u, v), forward adjacency (cruising) and reverse adjacency (trees toward a target)
    this.edgeOf = new Map();
    for (let e = 0; e < E; e++) {
      const k = this.src[e] * 65536 + this.dst[e], j = this.edgeOf.get(k);
      if (j === undefined || this.len[e] / this.mult[e] < this.len[j] / this.mult[j]) this.edgeOf.set(k, e);
    }
    const best = [...this.edgeOf.values()];
    const csr = (key) => {
      const off = new Int32Array(this.n + 1);
      for (const e of best) off[key(e) + 1]++;
      for (let i = 0; i < this.n; i++) off[i + 1] += off[i];
      const fill = off.slice(0, this.n), list = new Int32Array(best.length);
      for (const e of best) list[fill[key(e)]++] = e;
      return [off, list];
    };
    [this.outOff, this.outList] = csr((e) => this.src[e]);
    [this.inOff, this.inList] = csr((e) => this.dst[e]);
    // all edges (incl. parallel ones) leaving each node, for cruising
    this.allOut = Array.from({ length: this.n }, () => []);
    for (let e = 0; e < E; e++) this.allOut[this.src[e]].push(e);
    // spatial hash of intersections
    this.HC = 4; this.hw = Math.ceil(this.WX / this.HC); this.hh = Math.ceil(this.WZ / this.HC);
    this.hash = Array.from({ length: this.hw * this.hh }, () => []);
    for (let i = 0; i < this.n; i++) this.hash[this.cell(this.nx[i], this.nz[i])].push(i);
    // zones
    this.zones = D.zones; this.N = D.zones.length;
    this.lut = b64Bytes(D.zone_lut); this.lutW = D.lut_w; this.lutH = D.lut_h; this.lutCell = D.lut_cell;
    this.ringOff = D.ring_off; this.zoneRingOff = D.zone_ring_off; this.rx = q(D.ring_x); this.rz = q(D.ring_z);
    const sum = Array.from({ length: this.N }, () => [0, 0, 0]);
    this.zoneNodes = Array.from({ length: this.N }, () => []);
    for (let i = 0; i < this.n; i++) {
      const z = this.zoneOf(this.nx[i], this.nz[i]);
      sum[z][0] += this.nx[i]; sum[z][1] += this.nz[i]; sum[z][2]++; this.zoneNodes[z].push(i);
    }
    this.centers = sum.map(([x, z, c]) => (c ? [x / c, z / c] : [0, 0]));
    this.zoneScale = Math.sqrt(this.WX * this.WZ / this.N);
    const prof = typeof TLC_DATA !== 'undefined' ? TLC_DATA : null;   // build.py injects CAL_MPS just above
    const mps = typeof CAL_MPS !== 'undefined' ? CAL_MPS : (prof && prof.speed_day_mps) || 3.8;
    this.baseSpeed = mps / D.unit_m;
    this.ambientCongestion = false; this.demandScale = 0.5;
    this.trees = new Map(); this.heap = new Int32Array(this.n * 4); this.hkey = new Float64Array(this.n * 4);
  }
  cell(x, z) { return clamp(Math.floor(z / this.HC), 0, this.hh - 1) * this.hw + clamp(Math.floor(x / this.HC), 0, this.hw - 1); }
  zoneOf(x, z) {
    const i = clamp(Math.floor(x / this.lutCell), 0, this.lutW - 1), j = clamp(Math.floor(z / this.lutCell), 0, this.lutH - 1);
    return this.lut[j * this.lutW + i];
  }
  zoneName(i) { return this.zones[i].name; }
  zoneCenter(i) { return this.centers[i]; }
  zoneDist(a, b) { const [ax, az] = this.centers[a], [bx, bz] = this.centers[b]; return Math.hypot(bx - ax, bz - az) / this.zoneScale; }
  nearestNode(x, z) {
    const cx = clamp(Math.floor(x / this.HC), 0, this.hw - 1), cz = clamp(Math.floor(z / this.HC), 0, this.hh - 1);
    let best = -1, bd = Infinity;
    for (let r = 0; r < Math.max(this.hw, this.hh); r++) {
      for (let j = cz - r; j <= cz + r; j++) {
        if (j < 0 || j >= this.hh) continue;
        for (let i = cx - r; i <= cx + r; i++) {
          if (i < 0 || i >= this.hw || Math.max(Math.abs(i - cx), Math.abs(j - cz)) !== r) continue;
          for (const k of this.hash[j * this.hw + i]) {
            const d = (this.nx[k] - x) ** 2 + (this.nz[k] - z) ** 2;
            if (d < bd) { bd = d; best = k; }
          }
        }
      }
      if (best >= 0 && (r * this.HC) ** 2 > bd) break;
    }
    return best;
  }
  snap(x, z) { const k = this.nearestNode(x, z); return [this.nx[k], this.nz[k]]; }
  randomPoint(r) { const k = Math.floor(r() * this.n); return [this.nx[k], this.nz[k]]; }
  pointInZone(zi, r) { const ns = this.zoneNodes[zi], k = ns.length ? ns[Math.floor(r() * ns.length)] : Math.floor(r() * this.n); return [this.nx[k], this.nz[k]]; }
  // Dijkstra on the reverse graph: free-flow cost from every node to `target`, and each node's next hop.
  treeTo(target) {
    let t = this.trees.get(target);
    if (t) { this.trees.delete(target); this.trees.set(target, t); return t; }
    const n = this.n, dist = new Float64Array(n).fill(Infinity), next = new Int32Array(n).fill(-1);   // 64-bit: must match the heap keys exactly
    const heap = this.heap, key = this.hkey; let size = 0;
    const push = (v, k) => { let i = size++; while (i > 0) { const p = (i - 1) >> 1; if (key[p] <= k) break; heap[i] = heap[p]; key[i] = key[p]; i = p; } heap[i] = v; key[i] = k; };
    const pop = () => {
      const v = heap[0], last = heap[--size], lk = key[size]; let i = 0;
      for (;;) { let c = 2 * i + 1; if (c >= size) break; if (c + 1 < size && key[c + 1] < key[c]) c++; if (key[c] >= lk) break; heap[i] = heap[c]; key[i] = key[c]; i = c; }
      heap[i] = last; key[i] = lk; return v;
    };
    dist[target] = 0; push(target, 0);
    while (size) {
      const k0 = key[0], v = pop();
      if (k0 > dist[v]) continue;
      for (let p = this.inOff[v]; p < this.inOff[v + 1]; p++) {
        const e = this.inList[p], u = this.src[e], nd = dist[v] + this.len[e] / this.mult[e];
        if (nd < dist[u]) { dist[u] = nd; next[u] = v; if (size < heap.length) push(u, nd); }
      }
    }
    t = { dist, next };
    this.trees.set(target, t);
    if (this.trees.size > 160) this.trees.delete(this.trees.keys().next().value);
    return t;
  }
  edgePoints(e, out) {
    const m = this.mult[e], end = this.dst[e], b = this.geoOff[e + 1];
    for (let k = this.geoOff[e] + 1; k < b; k++) out.push(this.gx[k], this.gz[k], m, k === b - 1 ? end : -1);
  }
  lead(a) {
    const R = a.route || [];
    for (let k = a.ri || 0; k < R.length; k += 4) if (R[k + 3] >= 0) return [R.slice(a.ri, k + 4), R[k + 3]];
    const node = a.node >= 0 ? a.node : this.nearestNode(a.x, a.z);
    const at = Math.abs(this.nx[node] - a.x) < 1e-6 && Math.abs(this.nz[node] - a.z) < 1e-6;
    return [at ? [] : [this.nx[node], this.nz[node], 1, node], node];
  }
  route(a, tx, tz) {
    const [out, start] = this.lead(a), target = this.nearestNode(tx, tz);
    if (start === target) return out;
    const { dist, next } = this.treeTo(target);
    if (!Number.isFinite(dist[start])) return out;
    for (let u = start, guard = 0; u !== target && guard < this.n; guard++) {
      const v = next[u]; this.edgePoints(this.edgeOf.get(u * 65536 + v), out); u = v;
    }
    return out;
  }
  cruise(a, r) {
    let [out, node] = this.lead(a), prev = a.prevNode ?? -1;
    for (let s = 0; s < 3; s++) {
      let opts = this.allOut[node].filter((e) => this.dst[e] !== prev);
      if (!opts.length) opts = this.allOut[node];
      const e = opts[Math.floor(r() * opts.length)];
      this.edgePoints(e, out); prev = node; node = this.dst[e];
    }
    return out;
  }
  // Free-flow road cost (length / class multiplier) from a point to b, via the tree toward b.
  roadDist(ax, az, bx, bz) {
    const u = this.nearestNode(ax, az), v = this.nearestNode(bx, bz);
    if (u === v) return Math.hypot(bx - ax, bz - az);
    const d = this.treeTo(v).dist[u];
    return (Number.isFinite(d) ? d : 1e6) + Math.hypot(this.nx[u] - ax, this.nz[u] - az);
  }
}

function makeCity() {
  const qs = typeof location !== 'undefined' ? new URLSearchParams(location.search) : null;
  if (typeof STREETS_DATA !== 'undefined' && !(qs && qs.get('city') === 'grid')) return new StreetCity(STREETS_DATA);
  return new GridCity();
}
let CITY = null;
function city() { return CITY || (CITY = makeCity()); }
