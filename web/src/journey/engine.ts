import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { BUILDING_FS, BUILDING_VS, CAR_FS, CAR_VS, GROUND_FS, GROUND_VS, RIBBON_FS, RIBBON_VS, STREET_FS, STREET_VS } from './shaders';
import { DAY, DECIDE, T, lerp, smooth } from './timeline';
import type { CityGeo, Zone } from './types';

const FOG = new THREE.Color('#05070c');
const TAXI = new THREE.Color('#FFC20A'), RED = new THREE.Color('#E5352B'), GREY = new THREE.Color('#5d6b80');
const ROT: Record<string, number> = { manhattan: Math.PI / 2, chicago: Math.PI / 2, cambridge: 0 };

type V3 = [number, number, number];
export type Label = { id: string; text: string; tone?: 'red' | 'yellow' | 'violet' | 'dark' };
export type TrafficTable = { zoneIds: number[]; factor: number[][] } | null;

function rng(seed: number) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

/** The street network as a graph: CSR adjacency both ways, edge lengths, points along edges, shortest paths. */
class Graph {
  out: Int32Array; outStart: Int32Array; inn: Int32Array; inStart: Int32Array; len: Float32Array; cost: Float32Array;
  constructor(public g: CityGeo['graph']) {
    const N = g.nx.length, E = g.src.length;
    const csr = (key: Uint16Array) => {
      const start = new Int32Array(N + 1); for (let e = 0; e < E; e++) start[key[e] + 1]++;
      for (let i = 0; i < N; i++) start[i + 1] += start[i];
      const fill = start.slice(0, N), list = new Int32Array(E); for (let e = 0; e < E; e++) list[fill[key[e]]++] = e;
      return [list, start] as const;
    };
    [this.out, this.outStart] = csr(g.src); [this.inn, this.inStart] = csr(g.dst);
    this.len = new Float32Array(E); this.cost = new Float32Array(E);
    for (let e = 0; e < E; e++) {
      let L = 0; for (let k = g.geoOff[e] + 1; k < g.geoOff[e + 1]; k++) L += Math.hypot(g.gx[k] - g.gx[k - 1], g.gz[k] - g.gz[k - 1]);
      this.len[e] = Math.max(0.01, L); this.cost[e] = this.len[e] / (g.classSpeed[g.cls[e]] || 1);
    }
  }
  point(e: number, s: number): [number, number, number] {   // x, z, heading along edge e at distance s
    const g = this.g; let acc = 0;
    for (let k = g.geoOff[e] + 1; k < g.geoOff[e + 1]; k++) {
      const dx = g.gx[k] - g.gx[k - 1], dz = g.gz[k] - g.gz[k - 1], L = Math.hypot(dx, dz);
      if (acc + L >= s || k === g.geoOff[e + 1] - 1) { const f = L ? Math.min(1, (s - acc) / L) : 0; return [g.gx[k - 1] + dx * f, g.gz[k - 1] + dz * f, Math.atan2(dx, dz)]; }
      acc += L;
    }
    return [g.gx[g.geoOff[e]], g.gz[g.geoOff[e]], 0];
  }
  nearest(x: number, z: number) { let b = 0, bd = Infinity; for (let i = 0; i < this.g.nx.length; i++) { const d = (this.g.nx[i] - x) ** 2 + (this.g.nz[i] - z) ** 2; if (d < bd) { bd = d; b = i; } } return b; }
  /** Fastest route from every node to `target`: next edge to take, and its time. */
  toward(target: number) {
    const N = this.g.nx.length, dist = new Float32Array(N).fill(Infinity), next = new Int32Array(N).fill(-1);
    const heap: [number, number][] = [[0, target]]; dist[target] = 0;
    const push = (it: [number, number]) => { heap.push(it); let i = heap.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; } };
    const pop = () => { const top = heap[0], last = heap.pop() as [number, number]; if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } } return top; };
    while (heap.length) {
      const [d, u] = pop(); if (d > dist[u]) continue;
      for (let k = this.inStart[u]; k < this.inStart[u + 1]; k++) {
        const e = this.inn[k], v = this.g.src[e], nd = d + this.cost[e];
        if (nd < dist[v]) { dist[v] = nd; next[v] = e; push([nd, v]); }
      }
    }
    return { dist, next };
  }
  polyline(edges: number[]) {
    const g = this.g, pts: number[] = [];
    for (const e of edges) for (let k = g.geoOff[e]; k < g.geoOff[e + 1]; k++) pts.push(g.gx[k], g.gz[k]);
    return pts;
  }
}

function ribbon(routes: { pts: number[]; delay: number }[], width: number, y: number) {
  const pos: number[] = [], t: number[] = [], dl: number[] = [], idx: number[] = [];
  for (const r of routes) {
    const p = r.pts; let total = 0;
    for (let i = 2; i < p.length; i += 2) total += Math.hypot(p[i] - p[i - 2], p[i + 1] - p[i - 1]);
    let acc = 0;
    for (let i = 2; i < p.length; i += 2) {
      const ax = p[i - 2], az = p[i - 1], bx = p[i], bz = p[i + 1], L = Math.hypot(bx - ax, bz - az); if (L < 1e-4) continue;
      const nx = (-(bz - az) / L) * width / 2, nz = ((bx - ax) / L) * width / 2, o = pos.length / 3;
      pos.push(ax + nx, y, az + nz, ax - nx, y, az - nz, bx + nx, y, bz + nz, bx - nx, y, bz - nz);
      const t0 = acc / total, t1 = (acc + L) / total; t.push(t0, t0, t1, t1); dl.push(r.delay, r.delay, r.delay, r.delay);
      idx.push(o, o + 2, o + 1, o + 1, o + 2, o + 3); acc += L;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('aT', new THREE.Float32BufferAttribute(t, 1)); g.setAttribute('aDelay', new THREE.Float32BufferAttribute(dl, 1));
  g.setIndex(idx);
  return g;
}
const ribbonMat = (color: THREE.Color, dur = 1) => new THREE.ShaderMaterial({
  vertexShader: RIBBON_VS, fragmentShader: RIBBON_FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  uniforms: { uProg: { value: 0 }, uDur: { value: dur }, uAlpha: { value: 1 }, uColor: { value: color.clone() } },
});

/** One real city: buildings, streets, a fleet driving the street graph. */
class CityLayer {
  group = new THREE.Group();
  bMat: THREE.ShaderMaterial; sMat: THREE.ShaderMaterial; cMat: THREE.ShaderMaterial;
  graph: Graph; radius: number; reveal = 0;
  cars: { e: number; s: number; v: number }[] = []; carPos: Float32Array; carGeo: THREE.BufferGeometry;
  constructor(public geo: CityGeo, carCount: number) {
    this.group.rotation.y = ROT[geo.city] ?? 0;
    this.radius = Math.hypot(geo.WX, geo.WZ) / 2;
    const b = geo.buildings, bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.BufferAttribute(b.pos, 3)); bg.setAttribute('normal', new THREE.BufferAttribute(b.nor, 3));
    bg.setAttribute('uv', new THREE.BufferAttribute(b.uv, 2)); bg.setAttribute('info', new THREE.BufferAttribute(b.info, 4));
    bg.setIndex(new THREE.BufferAttribute(b.idx, 1));
    this.bMat = new THREE.ShaderMaterial({ vertexShader: BUILDING_VS, fragmentShader: BUILDING_FS, side: THREE.DoubleSide,
      uniforms: { uRise: { value: 0 }, uRadius: { value: this.radius }, uFlat: { value: new THREE.Vector4(0, 0, 1, 0) },
        uFog: { value: FOG }, uFogD: { value: 0.0016 }, uWin: { value: 1 }, uTime: { value: 0 } } });
    const bm = new THREE.Mesh(bg, this.bMat); bm.frustumCulled = false; this.group.add(bm);

    const s = geo.streets, sg = new THREE.BufferGeometry(), d = new Float32Array(s.pos.length / 3);
    for (let i = 0; i < d.length; i++) d[i] = Math.hypot(s.pos[i * 3], s.pos[i * 3 + 2]);
    sg.setAttribute('position', new THREE.BufferAttribute(s.pos, 3)); sg.setAttribute('aMajor', new THREE.BufferAttribute(s.major, 1));
    sg.setAttribute('aD', new THREE.BufferAttribute(d, 1));
    this.sMat = new THREE.ShaderMaterial({ vertexShader: STREET_VS, fragmentShader: STREET_FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uReveal: { value: 0 }, uAlpha: { value: 1 }, uFog: { value: FOG }, uFogD: { value: 0.0016 } } });
    const sl = new THREE.LineSegments(sg, this.sMat); sl.frustumCulled = false; this.group.add(sl);

    this.graph = new Graph(geo.graph);
    const r = rng(geo.nNodes), E = geo.graph.src.length, cols = new Float32Array(carCount * 3);
    this.carPos = new Float32Array(carCount * 3);
    for (let i = 0; i < carCount; i++) {
      const e = Math.floor(r() * E); this.cars.push({ e, s: r() * this.graph.len[e], v: 2.2 + r() * 2.6 });
      const k = r(), c = k < 0.55 ? [0.42, 0.52, 0.68] : k < 0.8 ? [1, 0.72, 0.05] : [0.3, 0.5, 1];
      cols.set(c, i * 3);
    }
    this.carGeo = new THREE.BufferGeometry();
    this.carGeo.setAttribute('position', new THREE.BufferAttribute(this.carPos, 3)); this.carGeo.setAttribute('aCol', new THREE.BufferAttribute(cols, 3));
    this.cMat = new THREE.ShaderMaterial({ vertexShader: CAR_VS, fragmentShader: CAR_FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uSize: { value: 1.6 }, uAlpha: { value: 0 } } });
    const pts = new THREE.Points(this.carGeo, this.cMat); pts.frustumCulled = false; this.group.add(pts);
  }
  drive(dt: number) {
    const G = this.graph, g = G.g, r = Math.random;
    this.cars.forEach((c, i) => {
      c.s += c.v * dt * (g.classSpeed[g.cls[c.e]] || 1);
      let guard = 0;
      while (c.s >= G.len[c.e] && guard++ < 8) {
        c.s -= G.len[c.e];
        const u = g.dst[c.e], a = G.outStart[u], b = G.outStart[u + 1];
        if (b <= a) { c.e = Math.floor(r() * g.src.length); c.s = 0; break; }
        let pick = G.out[a + Math.floor(r() * (b - a))];
        if (b - a > 1 && g.dst[pick] === g.src[c.e]) pick = G.out[a + ((pick - G.out[a] + 1) % (b - a))] ?? pick;   // avoid U-turns
        c.e = pick;
      }
      const [x, z] = G.point(c.e, c.s); this.carPos[i * 3] = x; this.carPos[i * 3 + 1] = 0.3; this.carPos[i * 3 + 2] = z;
    });
    (this.carGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  }
  set(rise: number, reveal: number, cars: number) {
    this.bMat.uniforms.uRise.value = rise; this.sMat.uniforms.uReveal.value = reveal * this.radius * 1.05; this.cMat.uniforms.uAlpha.value = cars;
    this.group.visible = rise > 0.001 || reveal > 0.001;
  }
  world(x: number, z: number, y = 0) { return this.group.localToWorld(new THREE.Vector3(x, y, z)); }
}

/** The decision scene in Manhattan: a rider, five candidate cars and their real fastest routes. */
class Decision {
  group = new THREE.Group(); rider: [number, number]; cands: { id: string; node: [number, number]; pts: number[]; km: number; mat: THREE.ShaderMaterial }[] = [];
  beacon: THREE.Mesh; ring: THREE.Mesh; car: THREE.Mesh; carMat: THREE.MeshBasicMaterial; pickPts: number[] = [];
  constructor(public city: CityLayer, focus: [number, number]) {
    const G = city.graph, g = G.g;
    const rn = G.nearest(focus[0], focus[1]); this.rider = [g.nx[rn], g.nz[rn]];
    const { dist, next } = G.toward(rn);
    const pool: { n: number; ang: number; d: number }[] = [];
    for (let i = 0; i < g.nx.length; i++) {
      const eu = Math.hypot(g.nx[i] - this.rider[0], g.nz[i] - this.rider[1]);
      if (dist[i] < Infinity && eu > 6 && eu < 26) pool.push({ n: i, ang: Math.atan2(g.nz[i] - this.rider[1], g.nx[i] - this.rider[0]), d: eu });
    }
    const chosen: typeof pool = [];
    for (let s = 0; s < 5; s++) {     // spread around the rider: one per 72-degree sector, the closest in each
      const lo = -Math.PI + (s * 2 * Math.PI) / 5, hi = lo + (2 * Math.PI) / 5;
      const inS = pool.filter((c) => c.ang >= lo && c.ang < hi).sort((a, b) => a.d - b.d);
      if (inS.length) chosen.push(inS[Math.min(inS.length - 1, s % 2 ? 3 : 1)]);
    }
    const route = (n: number) => { const es: number[] = []; let u = n, guard = 0; while (u !== rn && next[u] >= 0 && guard++ < 4000) { es.push(next[u]); u = g.dst[next[u]]; } return es; };
    const near = chosen.reduce((a, b) => (b.d < a.d ? b : a), chosen[0]);
    const best = chosen.filter((c) => c !== near).reduce((a, b) => (dist[b.n] < dist[a.n] ? b : a), chosen.find((c) => c !== near) ?? near);
    chosen.forEach((c, i) => {
      const es = route(c.n), pts = G.polyline(es), km = es.reduce((a, e) => a + G.len[e], 0) * city.geo.unitM / 1000;
      const id = c === near ? 'near' : c === best ? 'pick' : `c${i}`;
      const mat = ribbonMat(id === 'pick' ? TAXI : GREY, 1);
      const mesh = new THREE.Mesh(ribbon([{ pts, delay: 0 }], id === 'pick' ? 0.9 : 0.6, 0.12), mat); mesh.frustumCulled = false; this.group.add(mesh);
      this.cands.push({ id, node: [g.nx[c.n], g.nz[c.n]], pts, km, mat });
      if (id === 'pick') this.pickPts = pts;
    });
    const beam = new THREE.CylinderGeometry(0.35, 0.35, 60, 12, 1, true); beam.translate(0, 30, 0);
    this.beacon = new THREE.Mesh(beam, new THREE.MeshBasicMaterial({ color: TAXI, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.beacon.position.set(this.rider[0], 0, this.rider[1]); this.group.add(this.beacon);
    const rg = new THREE.RingGeometry(1.2, 1.7, 48); rg.rotateX(-Math.PI / 2);
    this.ring = new THREE.Mesh(rg, new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }));
    this.ring.position.set(this.rider[0], 0.2, this.rider[1]); this.group.add(this.ring);
    this.carMat = new THREE.MeshBasicMaterial({ color: TAXI, transparent: true, opacity: 0 });
    this.car = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.7, 1.8), this.carMat); this.group.add(this.car);
    city.group.add(this.group);
  }
  update(p: number, t: number) {
    const on = smooth(DECIDE.rider - 0.01, DECIDE.rider + 0.01, p) * (1 - smooth(T.decide[1] - 0.01, T.decide[1] + 0.02, p));
    (this.beacon.material as THREE.MeshBasicMaterial).opacity = on * (0.35 + 0.15 * Math.sin(t * 4));
    const pk = (t * 0.9) % 1, ring = this.ring.material as THREE.MeshBasicMaterial;
    ring.opacity = on * (1 - pk); this.ring.scale.setScalar(1 + pk * 3);
    const grow = smooth(DECIDE.routes[0], DECIDE.routes[1], p), dec = smooth(DECIDE.decide - 0.006, DECIDE.decide + 0.006, p), drive = smooth(DECIDE.drive[0], DECIDE.drive[1], p);
    this.cands.forEach((c, i) => {
      const u = c.mat.uniforms; u.uProg.value = Math.min(1, grow * 1.25 - i * 0.05);
      if (c.id === 'near') u.uColor.value.copy(GREY).lerp(RED, dec);
      u.uAlpha.value = on * (c.id === 'pick' ? 1 : c.id === 'near' ? 1 - dec * 0.4 : 1 - dec * 0.85);
    });
    // the chosen car drives its route
    const P = this.pickPts; let total = 0; for (let i = 2; i < P.length; i += 2) total += Math.hypot(P[i] - P[i - 2], P[i + 1] - P[i - 1]);
    let want = drive * total, x = P[0] ?? 0, z = P[1] ?? 0, h = 0;
    for (let i = 2; i < P.length; i += 2) {
      const L = Math.hypot(P[i] - P[i - 2], P[i + 1] - P[i - 1]);
      if (want <= L) { const f = L ? want / L : 0; x = P[i - 2] + (P[i] - P[i - 2]) * f; z = P[i - 1] + (P[i + 1] - P[i - 1]) * f; h = Math.atan2(P[i] - P[i - 2], P[i + 1] - P[i - 1]); break; }
      want -= L; x = P[i]; z = P[i + 1];
    }
    this.car.position.set(x, 0.45, z); this.car.rotation.y = h; this.carMat.opacity = on * smooth(DECIDE.routes[0], DECIDE.routes[0] + 0.01, p);
  }
}

/** Every official zone as a glass column: how slow its streets are, hour by hour, from real trip records. */
class DayColumns {
  group = new THREE.Group(); cols: { zone: Zone; mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; row: number[] | null }[] = [];
  constructor(city: CityLayer, table: NonNullable<TrafficTable>) {
    for (const zone of city.geo.zones) {
      const ring = zone.rings.reduce((a, b) => (b.length > a.length ? b : a), [] as number[]); if (ring.length < 6) continue;
      const sh = new THREE.Shape(); for (let i = 0; i < ring.length; i += 2) (i ? sh.lineTo : sh.moveTo).call(sh, ring[i], -ring[i + 1]);
      const g = new THREE.ExtrudeGeometry(sh, { depth: 1, bevelEnabled: false }); g.rotateX(-Math.PI / 2);
      const mat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, side: THREE.FrontSide });
      const mesh = new THREE.Mesh(g, mat); this.group.add(mesh);
      const k = table.zoneIds.indexOf(zone.id);
      this.cols.push({ zone, mesh, mat, row: k >= 0 ? table.factor[k] : null });
    }
    city.group.add(this.group);
  }
  update(on: number, hour: number) {
    this.group.visible = on > 0.001;
    const h0 = Math.floor(hour) % 24, h1 = (h0 + 1) % 24, f = hour - Math.floor(hour);
    for (const c of this.cols) {
      if (!c.row) { c.mesh.visible = false; continue; }
      const slow = 1 / lerp(c.row[h0], c.row[h1], f);                              // > 1: slower than the city's daytime mean
      const k = Math.min(1, Math.max(0, (slow - 0.55) / 0.9));
      c.mesh.scale.y = Math.max(0.05, on * (0.5 + k * k * 30));
      c.mat.color.setHSL(0.6 - 0.6 * k, 0.85, 0.22 + 0.2 * k); c.mat.opacity = on * (0.1 + 0.45 * k * k);
    }
  }
  slowest(hour: number, n = 3) {
    const h = Math.floor(hour) % 24;
    return this.cols.filter((c) => c.row).map((c) => ({ c, slow: 1 / (c.row as number[])[h] })).sort((a, b) => b.slow - a.slow).slice(0, n);
  }
}

export class JourneyEngine {
  renderer: THREE.WebGLRenderer; scene = new THREE.Scene(); camera = new THREE.PerspectiveCamera(40, 1, 1, 5000);
  composer: EffectComposer | null = null; bloom: UnrealBloomPass | null = null;
  cities = new Map<string, CityLayer>(); decision: Decision | null = null; day: DayColumns | null = null; batch: THREE.Mesh | null = null; batchMat = ribbonMat(TAXI, 0.35);
  p = 0; target = 0; t = 0; w = 1; h = 1;
  eye = new THREE.Vector3(0, 700, 1); look = new THREE.Vector3(); focus = new THREE.Vector3();
  labels: Record<string, HTMLElement | null> = {}; hour = 0;
  constructor(canvas: HTMLCanvasElement, private opts: { mobile: boolean; still: boolean; traffic: TrafficTable }) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: !opts.mobile, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, opts.mobile ? 1.25 : 1.6));
    this.renderer.setClearColor(FOG); this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene.fog = null;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(8000, 8000), new THREE.ShaderMaterial({ vertexShader: GROUND_VS, fragmentShader: GROUND_FS, uniforms: { uFog: { value: FOG } } }));
    ground.rotation.x = -Math.PI / 2; ground.position.y = -0.05; this.scene.add(ground);
    if (!opts.mobile) {
      this.composer = new EffectComposer(this.renderer);
      this.composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.85, 0.55, 0.32); this.composer.addPass(this.bloom);
      this.composer.addPass(new OutputPass());
    }
    this.camera.position.copy(this.eye);
  }
  addCity(geo: CityGeo) {
    if (this.cities.has(geo.city)) return;
    const layer = new CityLayer(geo, this.opts.mobile ? 900 : geo.city === 'manhattan' ? 2600 : 1800);
    this.cities.set(geo.city, layer); this.scene.add(layer.group); layer.group.updateMatrixWorld(true);
    if (geo.city === 'manhattan') {
      const z = geo.zones.find((zn) => /midtown center/i.test(zn.name)) ?? geo.zones.find((zn) => /midtown|times sq/i.test(zn.name)) ?? geo.zones[0];
      this.decision = new Decision(layer, [z.cx, z.cz]);
      this.focus.copy(layer.world(this.decision.rider[0], this.decision.rider[1]));
      if (this.opts.traffic) this.day = new DayColumns(layer, this.opts.traffic);
      // the whole city at once: 150 riders, each reached by a car a few blocks away along real streets
      const G = layer.graph, g = G.g, r = rng(42), routes: { pts: number[]; delay: number }[] = [];
      for (let i = 0; i < 150; i++) {
        const target = Math.floor(r() * g.nx.length), es: number[] = []; let u = target;
        for (let s = 0, n = 6 + Math.floor(r() * 16); s < n; s++) { const a = G.inStart[u], b = G.inStart[u + 1]; if (b <= a) break; const e = G.inn[a + Math.floor(r() * (b - a))]; es.unshift(e); u = g.src[e]; }
        if (es.length > 2) routes.push({ pts: G.polyline(es), delay: Math.hypot(g.nx[target], g.nz[target]) / layer.radius * 0.6 + r() * 0.05 });
      }
      this.batch = new THREE.Mesh(ribbon(routes, 0.7, 0.14), this.batchMat); this.batch.frustumCulled = false; layer.group.add(this.batch);
    }
  }
  setSize(w: number, h: number) {
    this.w = w; this.h = h; this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.composer?.setSize(w, h); this.bloom?.setSize(w / 2, h / 2);
  }
  /** Camera path, one key per moment of the story, through the focus point (Midtown) where it matters. */
  private keys(): [number, V3, V3][] {
    const F = this.focus, f = (dx: number, dy: number, dz: number): V3 => [F.x + dx, dy, F.z + dz], fc: V3 = [F.x, 0, F.z];
    const wide = this.w / this.h < 1 ? 1.9 : 1;          // portrait screens need to stand further back
    return [
      [0, [0, 640 * wide, 1], [0, 0, 0]],
      [0.07, [0, 560 * wide, 90], [0, 0, 0]],
      [0.16, [-260, 250 * wide, 340 * wide], [-50, 0, 0]],
      [0.24, f(150, 130 * wide, 190 * wide), fc],
      [0.31, f(50, 62 * wide, 62 * wide), fc],
      [0.40, f(-38, 58 * wide, 66 * wide), fc],
      [0.47, [40, 430 * wide, 380 * wide], [0, 0, 0]],
      [0.53, [-120, 300 * wide, 330 * wide], [0, 0, 0]],
      [0.62, [180, 250 * wide, 300 * wide], [0, 0, -10]],
      [0.67, [0, 420 * wide, 440 * wide], [0, 0, 0]],
      [0.73, [230, 230 * wide, 300 * wide], [0, 0, 0]],
      [0.78, [-170, 170 * wide, 250 * wide], [0, 0, 0]],
      [0.85, [170, 190 * wide, 220 * wide], [0, 0, 0]],
      [0.91, [-150, 140 * wide, 190 * wide], [0, 0, 0]],
      [1, [0, 900 * wide, 260 * wide], [0, 0, 0]],
    ];
  }
  frame(dt: number) {
    dt = Math.min(dt, 0.1);
    if (!this.opts.still) this.t += dt;
    this.p += (this.target - this.p) * (1 - Math.exp(-dt * 7));
    const p = this.p;
    // ---- cities
    const M = this.cities.get('manhattan'), C = this.cities.get('chicago'), B = this.cities.get('cambridge');
    const mOut = smooth(T.chicago[0], T.chicago[0] + 0.05, p);
    M?.set(smooth(T.rise[0], T.rise[1] - 0.01, p) * (1 - mOut), smooth(0, T.intro[1], p) * (1 - mOut), smooth(T.traffic[0], T.traffic[0] + 0.03, p) * (1 - smooth(T.day[1] - 0.04, T.day[1], p)));
    const cIn = smooth(T.chicago[0] + 0.01, T.chicago[0] + 0.07, p), cOut = smooth(T.cambridge[0], T.cambridge[0] + 0.05, p);
    C?.set(cIn * (1 - cOut), smooth(T.chicago[0], T.chicago[0] + 0.05, p) * (1 - cOut), smooth(T.chicago[0] + 0.05, T.chicago[0] + 0.08, p) * (1 - cOut));
    const bIn = smooth(T.cambridge[0] + 0.01, T.cambridge[0] + 0.07, p);
    B?.set(bIn, smooth(T.cambridge[0], T.cambridge[0] + 0.05, p), smooth(T.cambridge[0] + 0.05, T.cambridge[0] + 0.08, p));
    for (const c of this.cities.values()) if (c.group.visible && c.cMat.uniforms.uAlpha.value > 0.01 && !this.opts.still) c.drive(dt);
    // ---- Manhattan story pieces
    if (M) {
      const xr = smooth(T.decide[0], T.decide[0] + 0.04, p) * (1 - smooth(T.decide[1] - 0.01, T.decide[1] + 0.03, p));
      if (this.decision) {
        const [rx, rz] = this.decision.rider; M.bMat.uniforms.uFlat.value.set(rx, rz, 34, xr);
        this.decision.update(p, this.t);
      }
      this.batchMat.uniforms.uProg.value = (p - T.batch[0]) / (T.batch[1] - T.batch[0] - 0.02);
      this.batchMat.uniforms.uAlpha.value = 1 - smooth(T.batch[1] - 0.015, T.batch[1] + 0.02, p);
      if (this.batch) this.batch.visible = p > T.batch[0] - 0.01 && p < T.batch[1] + 0.03;
      const dayOn = smooth(DAY[0] - 0.02, DAY[0], p) * (1 - smooth(DAY[1], DAY[1] + 0.02, p));
      this.hour = Math.min(23.99, Math.max(0, ((p - DAY[0]) / (DAY[1] - DAY[0])) * 24));
      this.day?.update(dayOn, this.hour);
      // windows light up at night, dim in the day
      const night = dayOn > 0.01 ? 0.35 + 0.95 * (0.5 + 0.5 * Math.cos(((this.hour - 1) / 24) * Math.PI * 2)) : 1;
      M.bMat.uniforms.uWin.value = night;
    }
    // ---- camera
    const K = this.keys(); let i = 0; while (i < K.length - 2 && p > K[i + 1][0]) i++;
    const [p0, e0, l0] = K[i], [p1, e1, l1] = K[i + 1], k = smooth(p0, p1, p);
    const sway = this.opts.still ? 0 : Math.sin(this.t * 0.15) * 0.06;
    const ex = lerp(e0[0], e1[0], k), ez = lerp(e0[2], e1[2], k), lx = lerp(l0[0], l1[0], k), lz = lerp(l0[2], l1[2], k);
    const c = Math.cos(sway), s = Math.sin(sway), dx = ex - lx, dz = ez - lz;
    this.eye.set(lx + dx * c + dz * s, lerp(e0[1], e1[1], k), lz - dx * s + dz * c); this.look.set(lx, lerp(l0[1], l1[1], k), lz);
    this.camera.position.copy(this.eye); this.camera.lookAt(this.look);
    // ---- labels anchored in the city
    this.placeLabels(p);
    if (this.composer) this.composer.render(); else this.renderer.render(this.scene, this.camera);
  }
  private place(id: string, wp: THREE.Vector3 | null, alpha: number) {
    const el = this.labels[id]; if (!el) return;
    if (!wp || alpha < 0.01) { el.style.opacity = '0'; return; }
    const v = wp.clone().project(this.camera);
    if (v.z > 1) { el.style.opacity = '0'; return; }
    el.style.opacity = String(alpha);
    el.style.transform = `translate(-50%, -100%) translate(${((v.x + 1) / 2) * this.w}px, ${((1 - v.y) / 2) * this.h}px)`;
  }
  private placeLabels(p: number) {
    const M = this.cities.get('manhattan'), D = this.decision;
    const on = (a: number, b: number) => smooth(a, a + 0.008, p) * (1 - smooth(b - 0.008, b, p));
    if (M && D) {
      this.place('rider', M.world(D.rider[0], D.rider[1], 3), on(DECIDE.rider, T.decide[1]));
      for (const c of D.cands) this.place(c.id, M.world(c.node[0], c.node[1], 2), on(DECIDE.routes[0] + 0.01, c.id === 'pick' || c.id === 'near' ? T.decide[1] : DECIDE.decide));
    }
    if (M && this.day) {
      const dayOn = on(DAY[0], DAY[1]);
      this.day.slowest(this.hour).forEach(({ c, slow }, i) => {
        const el = this.labels[`z${i}`], pct = Math.round((slow - 1) * 100);
        const text = `${c.zone.name} · ${Math.abs(pct)}% ${pct >= 0 ? 'slower' : 'faster'} than usual`;
        if (el && el.textContent !== text) el.textContent = text;
        this.place(`z${i}`, M.world(c.zone.cx, c.zone.cz, 30), dayOn);
      });
    }
  }
  candidates() { return this.decision?.cands.map((c) => ({ id: c.id, km: c.km })) ?? []; }
  dispose() { this.renderer.dispose(); this.composer?.dispose(); }
}
