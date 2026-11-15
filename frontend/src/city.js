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
  const C = CFG.CELL, G = CFG.GRID, rnd = mulberry32(2026);
  const half = (i) => (isArterial(i) ? 1.7 : 0.95);

  // ---------------------------------------------------------------- island plate, embankment, water
  const base = new Geo();
  base.box(-16, W + 16, -5, 0, -16, W + 16, lin(PAL.road));
  const plate = R.mesh(base, 1, { order: 0 });
  plate.set(0, 0, 0, 0, 1, 1, 1, 0, 1, 1, 1, 1); plate.commit(1);
  R.water = { rect: new Float32Array([-1600, -1600, 3200 + W, 3200 + W]), y: -2.4, city: new Float32Array([W / 2, W / 2 + 16]),
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
function pinGeometry() {
  return new Geo().cylinder(0, 0, 0.12, 0, 6.2, lin('#2A2D31'), 6).sphere(0, 6.9, 0, 0.85, [1, 1, 1], 12, 8, 1);
}
