// =============================================================================
// The city model: an island grid in a river, built procedurally from the
// same road grid the dispatch engine drives on.
// =============================================================================
const PAL = {
  road: '#AEB6C0', plate: '#D6DBE1', slab: '#ECEEF0', curb: '#F6F7F8', park: '#C5D4BA', parkPath: '#E3E6DD',
  mark: '#FAFBFC', zebra: '#F4F5F6',
  clay: ['#F5F4F0', '#EFEEE9', '#F3F2EE', '#ECEBE6', '#F7F6F3'], glass: ['#D9E2EC', '#D2DCE8', '#E0E7EF'],
  brick: ['#E6D3C3', '#DFC6B4', '#E9DACB', '#D8BFAE', '#EADFD3'],
  hvac: '#DADCDF', tank: '#B39478', tankRoof: '#7E6A58', legs: '#5C5650',
  leaf: ['#A6BC98', '#9BB48E', '#B2C6A4', '#93AC87'], trunk: '#8C8277',
  glassCar: '#262C33', tyre: '#1F2327', head: '#FFF4D2', tail: '#C8232C', sign: '#FFFFFF',
};
const lin = (h) => srgb(h);

function isParkBlock(bx, bz) { return bx >= 10 && bx <= 14 && bz >= 3 && bz <= 7; }

function buildCity(R) {
  if (city().name !== 'grid') return buildStreetCity(R, city());
  return buildGridCity(R);
}

function buildGridCity(R) {
  const W = CFG.W, C = CFG.CELL, G = CFG.GRID, rnd = mulberry32(2026);
  const half = (i) => (isArterial(i) ? 1.7 : 0.95);

  // ---------------------------------------------------------------- island plate, embankment, water
  const base = new Geo();
  base.box(-16, W + 16, -5, 0, -16, W + 16, lin(PAL.road));
  const plate = R.mesh(base, 1, { order: 0 });
  plate.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); plate.commit(1);
  R.water = { rect: new Float32Array([-1600, -1600, 3200 + W, 3200 + W]), y: -2.4, city: new Float32Array([W / 2, W / 2, W / 2 + 16, W / 2 + 16]),
    deep: [0.60, 0.70, 0.77], shallow: [0.70, 0.79, 0.84] };

  // ---------------------------------------------------------------- unit-box layers
  const unit = new Geo().box(-0.5, 0.5, 0, 1, -0.5, 0.5, [1, 1, 1], 1);
  const flat = [], built = [], tanks = [], spires = [], streetTrees = [];
  const push = (arr, x, z, sx, sz, y0, h, hex, rot = 0) => { const c = lin(hex); arr.push([x, y0, z, sx, h, sz, rot, c[0], c[1], c[2]]); };

  // blocks, curbs, parks
  const parkTrees = [];
  for (let bz = 0; bz < G - 1; bz++) for (let bx = 0; bx < G - 1; bx++) {
    const x0 = bx * C + half(bx), x1 = (bx + 1) * C - half(bx + 1), z0 = bz * C + half(bz), z1 = (bz + 1) * C - half(bz + 1);
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2, sx = x1 - x0, sz = z1 - z0;
    const park = isParkBlock(bx, bz) || rnd() < 0.035;
    push(flat, cx, cz, sx, sz, 0, 0.36, park ? PAL.curb : PAL.slab);
    if (park) {
      push(flat, cx, cz, sx - 0.7, sz - 0.7, 0.36, 0.06, PAL.park);
      push(flat, cx, cz, sx - 0.7, 0.55, 0.36, 0.08, PAL.parkPath, (rnd() - 0.5) * 0.9);
      const n = 3 + Math.floor(rnd() * 4);
      for (let k = 0; k < n; k++) parkTrees.push([x0 + 0.9 + rnd() * (sx - 1.8), z0 + 0.9 + rnd() * (sz - 1.8)]);
      continue;
    }
    lots(bx, bz, x0, x1, z0, z1);
  }

  // lane markings on arterials, crosswalks where two arterials cross
  for (let i = 0; i < G; i++) if (isArterial(i)) {
    for (let s = 1.2; s < W - 1; s += 3.4) {
      push(flat, s, i * C, 1.7, 0.16, 0.0, 0.035, PAL.mark);
      push(flat, i * C, s, 0.16, 1.7, 0.0, 0.035, PAL.mark);
    }
  }
  for (let i = 0; i < G; i++) for (let j = 0; j < G; j++) if (isArterial(i) && isArterial(j)) {
    const x = i * C, z = j * C, off = 2.25;
    for (let k = -2; k <= 2; k++) {
      push(flat, x + k * 0.62, z - off, 0.34, 1.0, 0, 0.04, PAL.zebra); push(flat, x + k * 0.62, z + off, 0.34, 1.0, 0, 0.04, PAL.zebra);
      push(flat, x - off, z + k * 0.62, 1.0, 0.34, 0, 0.04, PAL.zebra); push(flat, x + off, z + k * 0.62, 1.0, 0.34, 0, 0.04, PAL.zebra);
    }
  }

  // ---------------------------------------------------------------- buildings
  function centers(x, z) {
    const g = (cx, cz, s) => Math.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2 * s * s));
    return 30 * g(W * 0.5, W * 0.5, 26) + 20 * g(W * 0.2, W * 0.84, 20) + 7 * g(W * 0.74, W * 0.22, 34);
  }
  function lots(bx, bz, x0, x1, z0, z1) {
    const sx = x1 - x0, sz = z1 - z0, cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
    const peak = centers(cx, cz), r = rnd();
    let parts;
    if (peak > 26 && r < 0.75) parts = [[cx, cz, sx, sz]];
    else if (r < 0.3) parts = [[cx, cz, sx, sz]];
    else if (r < 0.62) parts = [[cx - sx / 4, cz, sx / 2, sz], [cx + sx / 4, cz, sx / 2, sz]];
    else parts = [[cx - sx / 4, cz - sz / 4, sx / 2, sz / 2], [cx + sx / 4, cz - sz / 4, sx / 2, sz / 2], [cx - sx / 4, cz + sz / 4, sx / 2, sz / 2], [cx + sx / 4, cz + sz / 4, sx / 2, sz / 2]];
    for (const [px, pz, w, d] of parts) {
      if (rnd() < 0.06) { streetTrees.push([px, pz]); continue; }
      building(px, pz, w - 0.65, d - 0.65, peak);
    }
    // street trees along the curb of quieter blocks
    if (peak < 12 && rnd() < 0.5) for (let k = 0; k < 3; k++) streetTrees.push([x0 + 0.35 + rnd() * (sx - 0.7), rnd() < 0.5 ? z0 + 0.3 : z1 - 0.3]);
  }
  function building(x, z, w, d, peak) {
    const y = 0.36, tall = peak > 14;
    let h = 1.1 + peak * (0.3 + 0.7 * rnd()) + 2.6 * rnd() * rnd();
    if (peak > 22 && rnd() < 0.14) h *= 1.5;                         // the odd supertall                       // the odd supertall
    const col = tall && rnd() < 0.65 ? PAL.glass[Math.floor(rnd() * 3)]
      : h < 6 && rnd() < 0.55 ? PAL.brick[Math.floor(rnd() * 5)] : PAL.clay[Math.floor(rnd() * 5)];
    if (h < 6) {
      push(built, x, z, w, d, y, h, col);
      roof(x, z, w, d, y + h, h);
    } else if (h < 16) {
      const ph = 1.6 + rnd() * 1.6, inset = 0.72 + rnd() * 0.16;
      push(built, x, z, w, d, y, ph, col);
      push(built, x, z, w * inset, d * inset, y + ph, h - ph, col);
      roof(x, z, w * inset, d * inset, y + h, h);
    } else {
      const ph = 2 + rnd() * 2, i1 = 0.74 + rnd() * 0.12, i2 = 0.62 + rnd() * 0.12, top = h * (0.12 + rnd() * 0.08);
      push(built, x, z, w, d, y, ph, col);
      push(built, x, z, w * i1, d * i1, y + ph, h - ph - top, col);
      push(built, x, z, w * i1 * i2, d * i1 * i2, y + h - top, top, col);
      if (h > 34 || rnd() < 0.18) spires.push([x, z, y + h, 2 + rnd() * (h > 34 ? 8 : 3)]);
      else push(built, x, z, w * i1 * i2 * 0.55, d * i1 * i2 * 0.55, y + h, 0.9, PAL.hvac);
    }
  }
  function roof(x, z, w, d, top, h) {
    const r = rnd();
    if (h > 2 && h < 9 && r < 0.09 && w > 2.4 && d > 2.4) tanks.push([x + (rnd() - 0.5) * w * 0.4, z + (rnd() - 0.5) * d * 0.4, top]);
    else if (r < 0.6) {
      const n = 1 + Math.floor(rnd() * 3);
      for (let k = 0; k < n; k++) push(built, x + (rnd() - 0.5) * w * 0.5, z + (rnd() - 0.5) * d * 0.5, 0.5 + rnd() * w * 0.25, 0.5 + rnd() * d * 0.25, top, 0.25 + rnd() * 0.35, PAL.hvac);
    }
  }

  const commitBoxes = (arr, opts) => {
    const L = R.mesh(unit, arr.length, opts);
    arr.forEach((b, i) => L.set(i, ...b, 1));
    L.commit(arr.length);
    return L;
  };
  commitBoxes(flat, { cast: false, order: 1 });
  commitBoxes(built, { cast: true, facade: true, order: 2 });

  // ---------------------------------------------------------------- water towers, spires, trees
  const tank = new Geo();
  for (const [lx, lz] of [[-0.42, -0.42], [0.42, -0.42], [0.42, 0.42], [-0.42, 0.42]]) tank.box(lx - 0.05, lx + 0.05, 0, 0.9, lz - 0.05, lz + 0.05, lin(PAL.legs));
  tank.box(-0.55, 0.55, 0.85, 0.92, -0.55, 0.55, lin(PAL.legs));
  tank.cylinder(0, 0, 0.6, 0.92, 2.1, lin(PAL.tank), 12);
  tank.cone(0, 0, 0.66, 2.1, 2.7, lin(PAL.tankRoof), 12);
  const tk = R.mesh(tank, tanks.length, { cast: true, order: 3 });
  tanks.forEach(([x, z, y], i) => tk.set(i, x, y, z, 1, 1, 1, rnd() * 6, 1, 1, 1, 1)); tk.commit(tanks.length);

  const spire = new Geo().cylinder(0, 0, 0.12, 0, 1, lin('#D7DCE2'), 6).box(-0.35, 0.35, 0, 0.12, -0.35, 0.35, lin('#D7DCE2'));
  const sp = R.mesh(spire, spires.length, { cast: true, order: 3 });
  spires.forEach(([x, z, y, h], i) => sp.set(i, x, y, z, 1, h, 1, 0, 1, 1, 1, 1)); sp.commit(spires.length);

  const tree = new Geo();
  tree.cylinder(0, 0, 0.12, 0, 0.8, lin(PAL.trunk), 6);
  tree.sphere(0, 1.35, 0, 0.75, [1, 1, 1], 8, 5, 1, 1.05);
  const allTrees = [...parkTrees.map((t) => [...t, 1]), ...streetTrees.map((t) => [...t, 0.7])];
  const tr = R.mesh(tree, allTrees.length, { cast: true, order: 3 });
  allTrees.forEach(([x, z, s], i) => {
    const k = s * (0.75 + rnd() * 0.6), c = lin(PAL.leaf[Math.floor(rnd() * PAL.leaf.length)]);
    tr.set(i, x, 0.42, z, k, k * (0.85 + rnd() * 0.4), k, rnd() * 6, c[0], c[1], c[2], 1);
  });
  tr.commit(allTrees.length);
  return { buildings: built.length, trees: allTrees.length, tanks: tanks.length };
}

// ---------------------------------------------------------------- moving things
function carGeometry() {
  const g = new Geo(), dark = lin(PAL.glassCar), tyre = lin(PAL.tyre);
  g.box(-0.62, 0.62, 0.2, 0.62, -1.28, 1.28, [1, 1, 1], 1);                                  // body (instance colour)
  g.frustum([-0.6, 0.6, -0.78, 0.5], [-0.5, 0.5, -0.56, 0.24], 0.62, 1.04, dark);             // glasshouse
  g.box(-0.5, 0.5, 1.04, 1.09, -0.56, 0.24, [1, 1, 1], 1);                                     // roof panel
  g.box(-0.2, 0.2, 1.09, 1.27, -0.26, -0.04, lin(PAL.sign));                                   // taxi roof sign
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) g.cylinder(sz * 0.84, 0.27, 0.27, sx * 0.53, sx * 0.71, tyre, 9, 0, 'x');
  for (const s of [-1, 1]) {
    g.box(s * 0.48 - 0.12, s * 0.48 + 0.12, 0.4, 0.52, 1.27, 1.3, lin(PAL.head));
    g.box(s * 0.48 - 0.12, s * 0.48 + 0.12, 0.42, 0.52, -1.3, -1.27, lin(PAL.tail));
  }
  return g;
}
// The same car seen from far away: body and glasshouse only (a tenth of the triangles).
function carGeometryFar() {
  return new Geo().box(-0.62, 0.62, 0.2, 0.62, -1.28, 1.28, [1, 1, 1], 1).frustum([-0.6, 0.6, -0.78, 0.5], [-0.5, 0.5, -0.56, 0.24], 0.62, 1.04, lin(PAL.glassCar));
}
function pinGeometry() {
  return new Geo().cylinder(0, 0, 0.12, 0, 6.2, lin('#2A2D31'), 6).sphere(0, 6.9, 0, 0.85, [1, 1, 1], 12, 8, 1);
}

// =============================================================================
// Real cities: land from the official zone polygons, streets, bridges and parks from
// OpenStreetMap, and every building from the city's official footprints, extruded from its
// real outline to its real height.
// =============================================================================
function triangulate(pts) {          // ear clipping for a simple polygon [[x, z], ...]; returns index triples
  const n = pts.length; if (n < 3) return [];
  let area = 0; for (let i = 0; i < n; i++) { const [x0, z0] = pts[i], [x1, z1] = pts[(i + 1) % n]; area += x0 * z1 - x1 * z0; }
  const V = [...Array(n).keys()]; if (area < 0) V.reverse();
  const cross = (a, b, c) => (pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (pts[b][1] - pts[a][1]) * (pts[c][0] - pts[a][0]);
  const out = [];
  // Walk around the polygon clipping ears, continuing from the last clip instead of restarting (O(n^2), not O(n^3)).
  for (let i = 0, stall = 0; V.length > 3 && stall < V.length;) {
    const m = V.length; i %= m;
    const a = V[(i + m - 1) % m], b = V[i], c = V[(i + 1) % m];
    let ear = cross(a, b, c) > 1e-9;
    if (ear) {
      const [ax, az] = pts[a], [bx, bz] = pts[b], [cx, cz] = pts[c];
      const x0 = Math.min(ax, bx, cx), x1 = Math.max(ax, bx, cx), z0 = Math.min(az, bz, cz), z1 = Math.max(az, bz, cz);
      for (const q of V) {
        if (q === a || q === b || q === c) continue;
        const [qx, qz] = pts[q]; if (qx < x0 || qx > x1 || qz < z0 || qz > z1) continue;
        if (cross(a, b, q) >= 0 && cross(b, c, q) >= 0 && cross(c, a, q) >= 0) { ear = false; break; }
      }
    }
    if (ear) { out.push([a, b, c]); V.splice(i, 1); stall = 0; i = Math.max(0, i - 1); } else { i++; stall++; }
  }
  for (let i = 1; i + 1 < V.length; i++) out.push([V[0], V[i], V[i + 1]]);   // degenerate remainder: fan it
  return out;
}

// Extrude many footprints into one static mesh, written straight into typed arrays.
const MINOR_M = 24, LOD_FAR = 260;          // buildings under 24 m are drawn within 260 units (about 8 km) of the camera
function extrudeBuildings(B) {
  const counts = b64Bytes(B.counts), X = b64U16(B.x), Z = b64U16(B.z), H = b64U16(B.h), Y = b64U16(B.year);
  let nv = 0, ni = 0;
  for (let i = 0; i < B.n; i++) { const k = counts[i]; nv += 5 * k; ni += 6 * k + 3 * (k - 2); }
  const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3), col = new Float32Array(nv * 3), mask = new Float32Array(nv);
  const idx = new Uint32Array(ni), ids = new Float32Array(nv), minor = new Uint8Array(nv);   // minor: low-rise, skipped far away
  let v = 0, t = 0, o = 0, tallest = 0;
  const put = (x, y, z, nx, ny, nz, c, id) => {
    pos[v * 3] = x; pos[v * 3 + 1] = y; pos[v * 3 + 2] = z; nor[v * 3] = nx; nor[v * 3 + 1] = ny; nor[v * 3 + 2] = nz;
    col[v * 3] = c[0]; col[v * 3 + 1] = c[1]; col[v * 3 + 2] = c[2]; ids[v] = id; return v++;
  };
  const roofTint = (c) => [c[0] * 0.93, c[1] * 0.93, c[2] * 0.95];
  for (let b = 0; b < B.n; b++) {
    const k = counts[b], h = Math.max(0.12, H[b] / 1000), hm = h * B.unit_m, yr = Y[b];
    tallest = Math.max(tallest, hm);
    const pts = []; let cx = 0, cz = 0;
    for (let j = 0; j < k; j++) { const p = [X[o + j] / 20, Z[o + j] / 20]; pts.push(p); cx += p[0] / k; cz += p[1] / k; }
    o += k;
    const c = lin(hm > 90 ? PAL.glass[b % 3] : yr && yr < 1940 && hm < 40 ? PAL.brick[b % 5] : PAL.clay[b % 5]);
    const id = (b * 0.6180339887) % 1;                        // per-building hash: which windows are lit at night
    const v0 = v;
    for (let j = 0; j < k; j++) {                              // walls
      const [x0, z0] = pts[j], [x1, z1] = pts[(j + 1) % k];
      let nx = z1 - z0, nz = -(x1 - x0); const L = Math.hypot(nx, nz) || 1; nx /= L; nz /= L;
      if (nx * ((x0 + x1) / 2 - cx) + nz * ((z0 + z1) / 2 - cz) < 0) { nx = -nx; nz = -nz; }
      const a = put(x0, 0.06, z0, nx, 0, nz, c, id), bb = put(x1, 0.06, z1, nx, 0, nz, c, id);
      const cc = put(x1, h, z1, nx, 0, nz, c, id), d = put(x0, h, z0, nx, 0, nz, c, id);
      idx[t++] = a; idx[t++] = bb; idx[t++] = cc; idx[t++] = a; idx[t++] = cc; idx[t++] = d;
    }
    const r0 = v, rc = roofTint(c);                            // roof
    for (const [x, z] of pts) put(x, h, z, 0, 1, 0, rc, id);
    const tris = triangulate(pts);
    for (let q = 0; q < k - 2; q++) {
      const tr = tris[q] || [0, q + 1, q + 2];
      idx[t++] = r0 + tr[0]; idx[t++] = r0 + tr[1]; idx[t++] = r0 + tr[2];
    }
    if (hm < MINOR_M) minor.fill(1, v0, v);
  }
  return { geo: { pos, nor, col, mask, idx: idx.subarray(0, t), count: v, ids, minor }, tallest, n: B.n };
}

const TILE = 32;                     // static meshes are culled in tiles of 32 units (about 1 km)
function buildStreetCity(R, C) {
  const rnd = mulberry32(2026), WX = C.WX, WZ = C.WZ;
  const up = (geo, a, b, c, col) => {          // a triangle facing +y regardless of input winding
    const ny = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]);
    if (ny >= 0) geo.tri(a, b, c, col); else geo.tri(a, c, b, col);
  };
  const ring = (k) => { const a = C.ringOff[k], b = C.ringOff[k + 1], out = []; for (let i = a; i < b; i++) out.push([C.rx[i], C.rz[i]]); return out; };
  const parkZones = new Set(C.zones.map((z, i) => (/^(Central Park|Grant Park|Millenium Park|Millennium Park)$/.test(z.name) ? i : -1)).filter((i) => i >= 0));

  // ---------------------------------------------------------------- land, embankment, surroundings
  performance.mark('orca-land');
  const land = new Geo(), slab = lin(PAL.slab), bank = lin('#C9CED5'), park = lin(PAL.park), outside = lin('#DDE1E5');
  const east = [];                                            // shoreline samples, for a lake to the east
  for (let zi = 0; zi < C.N; zi++) {
    for (let r = C.zoneRingOff[zi]; r < C.zoneRingOff[zi + 1]; r++) {
      const pts = ring(r);
      if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
      const isPark = parkZones.has(zi), col = isPark ? park : slab, y = isPark ? 0.05 : 0;
      for (const [a, b, c] of triangulate(pts)) up(land, [pts[a][0], y, pts[a][1]], [pts[b][0], y, pts[b][1]], [pts[c][0], y, pts[c][1]], col);
      for (let i = 0; i < pts.length; i++) {
        const [x0, z0] = pts[i], [x1, z1] = pts[(i + 1) % pts.length];
        land.quad([x0, 0, z0], [x1, 0, z1], [x1, -3.2, z1], [x0, -3.2, z0], bank);
        land.quad([x0, -3.2, z0], [x1, -3.2, z1], [x1, 0, z1], [x0, 0, z0], bank);
        east.push(pts[i]);
      }
    }
  }
  if (C.surround === 'lake-east') {
    // The city continues inland beyond the simulated area; only Lake Michigan, to the east, is water.
    const bins = new Map(), B = 4;
    for (const [x, z] of east) { const k = Math.floor(z / B); bins.set(k, Math.max(bins.get(k) ?? -1e9, x)); }
    const keys = [...bins.keys()].sort((a, b) => a - b), shore = keys.map((k) => [bins.get(k), k * B + B / 2]);
    const far = 2400, y = -0.35;

    for (let i = 1; i < shore.length; i++) {                  // mainland west of the shoreline, in strips
      const [xa, za] = shore[i - 1], [xb, zb] = shore[i];
      up(land, [-far, y, za], [xa, y, za], [xb, y, zb], outside); up(land, [-far, y, za], [xb, y, zb], [-far, y, zb], outside);
    }
    up(land, [-far, y, -far], [shore[0][0], y, -far], [shore[0][0], y, shore[0][1]], outside);
    up(land, [-far, y, -far], [shore[0][0], y, shore[0][1]], [-far, y, shore[0][1]], outside);
    const last = shore[shore.length - 1];
    up(land, [-far, y, last[1]], [last[0], y, last[1]], [last[0], y, WZ + far], outside);
    up(land, [-far, y, last[1]], [last[0], y, WZ + far], [-far, y, WZ + far], outside);
  }
  const landMesh = R.mesh(land, 1, { order: 0, tile: TILE }); landMesh.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); landMesh.commit(1);
  R.water = { rect: new Float32Array([-1600, -1600, 3200 + WX, 3200 + WZ]), y: -2.4, city: new Float32Array([WX / 2, WZ / 2, WX / 2, WZ / 2]),
    deep: [0.60, 0.70, 0.77], shallow: [0.70, 0.79, 0.84] };

  // ---------------------------------------------------------------- parks from OpenStreetMap
  performance.measure('orca-land', 'orca-land'); performance.mark('orca-parks');
  const green = new Geo(), parkCol = lin(PAL.park), parkTrees = [];
  for (const flat of C.parks) {
    const pts = []; for (let i = 0; i + 1 < flat.length; i += 2) pts.push([flat[i], flat[i + 1]]);
    if (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
    if (pts.length < 3 || pts.length > 400) continue;
    for (const [a, b, c] of triangulate(pts)) up(green, [pts[a][0], 0.03, pts[a][1]], [pts[b][0], 0.03, pts[b][1]], [pts[c][0], 0.03, pts[c][1]], parkCol);
    let ar = 0; for (let i = 0; i < pts.length; i++) { const [x0, z0] = pts[i], [x1, z1] = pts[(i + 1) % pts.length]; ar += x0 * z1 - x1 * z0; }
    const nTrees = Math.min(60, Math.floor(Math.abs(ar) / 2 / 6));
    let xs = Infinity, xe = -Infinity, zs = Infinity, ze = -Infinity;
    for (const [x, z] of pts) { xs = Math.min(xs, x); xe = Math.max(xe, x); zs = Math.min(zs, z); ze = Math.max(ze, z); }
    for (let k = 0, got = 0; k < nTrees * 4 && got < nTrees; k++) {
      const x = xs + rnd() * (xe - xs), z = zs + rnd() * (ze - zs);
      let inside = false;
      for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const [xi, zi] = pts[i], [xj, zj] = pts[j];
        if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
      }
      if (inside) { parkTrees.push([x, z]); got++; }
    }
  }
  performance.measure('orca-parks', 'orca-parks');
  if (green.count) { const gm = R.mesh(green, 1, { order: 0, tile: TILE }); gm.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); gm.commit(1); }

  // ---------------------------------------------------------------- streets: ribbons along the real geometry
  performance.mark('orca-roads');
  const roads = new Geo(), seen = new Set();
  const HALF = [0.95, 0.8, 0.62, 0.55, 0.42, 0.34, 0.34, 0.25, 0.45, 0.45, 0.42, 0.38, 0.34];
  const colMajor = lin('#9DA6B1'), colMinor = lin('#B0B8C2'), colHwy = lin('#8E98A4');   // street contrast against the pale ground
  const ribbon = (geo, x0, z0, x1, z1, w, y, col) => {
    const L = Math.hypot(x1 - x0, z1 - z0); if (L < 1e-4) return;
    const dx = (x1 - x0) / L, dz = (z1 - z0) / L, nx = -dz * w, nz = dx * w;
    x0 -= dx * w * 0.6; z0 -= dz * w * 0.6; x1 += dx * w * 0.6; z1 += dz * w * 0.6;        // overlap the joints
    const a = [x0 + nx, y, z0 + nz], b = [x1 + nx, y, z1 + nz], c = [x1 - nx, y, z1 - nz], d = [x0 - nx, y, z0 - nz];
    up(geo, a, b, c, col); up(geo, a, c, d, col);
  };
  for (let e = 0; e < C.src.length; e++) {
    const u = C.src[e], v = C.dst[e], key = Math.min(u, v) + '_' + Math.max(u, v) + '_' + Math.round(C.len[e] * 10);
    if (seen.has(key)) continue; seen.add(key);
    const cls = C.cls[e], w = HALF[cls] ?? 0.34, col = cls <= 1 ? colHwy : C.classMajor[cls] ? colMajor : colMinor, y = cls <= 1 ? 0.07 : 0.04;
    for (let k = C.geoOff[e]; k < C.geoOff[e + 1] - 1; k++) ribbon(roads, C.gx[k], C.gz[k], C.gx[k + 1], C.gz[k + 1], w, y, col);
  }
  const roadMesh = R.mesh(roads, 1, { order: 1, glow: [1.0, 0.78, 0.45], tile: TILE }); performance.measure('orca-roads', 'orca-roads'); roadMesh.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); roadMesh.commit(1);

  // ---------------------------------------------------------------- bridges over the water: deck, parapets, piers
  const deck = new Geo(), deckCol = lin('#A7AFB9'), sideCol = lin('#7E8792');
  const piers = [];
  for (const b of C.bridges) {
    const w = HALF[b.cls] ?? 0.6, y = 1.3;
    for (let i = 0; i + 3 < b.p.length; i += 2) {
      const [x0, z0, x1, z1] = [b.p[i], b.p[i + 1], b.p[i + 2], b.p[i + 3]];
      ribbon(deck, x0, z0, x1, z1, w, y, deckCol);
      const L = Math.hypot(x1 - x0, z1 - z0); if (L < 1e-4) continue;
      const nx = -(z1 - z0) / L * w, nz = (x1 - x0) / L * w;
      deck.quad([x0 + nx, y - 0.45, z0 + nz], [x1 + nx, y - 0.45, z1 + nz], [x1 + nx, y + 0.12, z1 + nz], [x0 + nx, y + 0.12, z0 + nz], sideCol);
      deck.quad([x0 - nx, y - 0.45, z0 - nz], [x1 - nx, y - 0.45, z1 - nz], [x1 - nx, y + 0.12, z1 - nz], [x0 - nx, y + 0.12, z0 - nz], sideCol);
      if (i % 8 === 0 && C.zoneOf(x0, z0) >= 0) piers.push([x0, z0]);
    }
  }
  if (deck.count) { const dm = R.mesh(deck, 1, { cast: true, order: 1, tile: TILE }); dm.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); dm.commit(1); }
  const pier = new Geo().box(-0.3, 0.3, -3.2, 1.0, -0.3, 0.3, lin('#8E97A1'));
  const pm = R.mesh(pier, Math.max(1, piers.length), { cast: true, order: 1 });
  piers.forEach(([x, z], i) => pm.set(i, x, 0, z, 1, 1, 1, 0, 1, 1, 1, 1)); pm.commit(piers.length);

  // ---------------------------------------------------------------- buildings: real outlines, real heights
  let tallest = 0, nb = 0;
  if (typeof BUILDINGS_DATA !== 'undefined' && BUILDINGS_DATA.city === C.name) {
    performance.mark('orca-extrude');
    const ex = extrudeBuildings(BUILDINGS_DATA);
    performance.measure('orca-extrude', 'orca-extrude');
    tallest = ex.tallest; nb = ex.n;
    performance.mark('orca-bmesh');
    const BL = R.mesh(ex.geo, 1, { cast: true, facade: 2, order: 2, tile: TILE, lod: LOD_FAR });
    performance.measure('orca-buildings-upload', 'orca-bmesh');
    BL.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); BL.commit(1);
  }

  // ---------------------------------------------------------------- trees: big parks and the OSM parks
  performance.mark('orca-trees');
  const trees = [...parkTrees];
  for (const pz of parkZones) for (let k = 0, got = 0; k < 8000 && got < 900; k++) {
    const x = rnd() * WX, z = rnd() * WZ;
    if (C.zoneOf(x, z) !== pz) continue;
    const [sx, sz] = C.snap(x, z);
    if (Math.hypot(sx - x, sz - z) > 1.2) { trees.push([x, z]); got++; }
  }
  const tree = new Geo();
  tree.cylinder(0, 0, 0.06, 0, 0.35, lin(PAL.trunk), 6);
  tree.sphere(0, 0.62, 0, 0.36, [1, 1, 1], 8, 5, 1, 1.05);
  const tr = R.mesh(tree, Math.max(1, trees.length), { cast: true, order: 3 });
  trees.forEach(([x, z], i) => {
    const k = 0.8 + rnd() * 0.6, c = lin(PAL.leaf[Math.floor(rnd() * PAL.leaf.length)]);
    tr.set(i, x, 0.05, z, k, k * (0.85 + rnd() * 0.4), k, rnd() * 6, c[0], c[1], c[2], 1);
  });
  tr.commit(trees.length);
  performance.measure('orca-trees', 'orca-trees');
  return { buildings: nb, trees: trees.length, tanks: 0, tallest, bridges: C.bridges.length, parks: C.parks.length };
}
