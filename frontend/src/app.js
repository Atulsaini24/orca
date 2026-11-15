// =============================================================================
// ORCA control room: data sources, live drawing, input, console and guided tour.
// Bundled after sim.js (engine), gl.js (renderer) and city.js (model).
// =============================================================================
const W = CFG.W, ZN = CFG.ZONES * CFG.ZONES, MAX_AGENTS = 4000, MAX_REQ = 5000;
const $ = (s) => document.querySelector(s);
const QS = new URLSearchParams(location.search);
const CAPTURE = QS.has('capture');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches && !CAPTURE;
const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const hhmm = (c) => { c = Math.floor(c) % 86400; return `${String(Math.floor(c / 3600)).padStart(2, '0')}:${String(Math.floor((c % 3600) / 60)).padStart(2, '0')}`; };
const car = (i) => `Car ${String(i).padStart(4, '0')}`;
const fmtInt = (n) => Math.round(n).toLocaleString('en-US');
const clamp01 = (v) => Math.max(0, Math.min(1, v));
const easeOutBack = (t) => { const c = 1.7; return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2; };
const easeOut = (t) => 1 - (1 - t) ** 3;

// ----------------------------------------------------------------------------- data sources
class Emitter { constructor() { this.l = {}; } on(e, f) { (this.l[e] ||= []).push(f); } emit(e, d) { for (const f of this.l[e] || []) f(d); } }

class DemoSource extends Emitter {
  constructor() {
    super();
    this.kind = 'demo'; this.view = 'smart'; this.speed = 20; this.paused = false; this.fleet = 1200; this.demand = 1; this.seq = 0; this.acc = 0;
    const n = MAX_AGENTS;
    this.buf = { x: new Float32Array(n), z: new Float32Array(n), h: new Uint8Array(n), s: new Uint8Array(n), tx: new Float32Array(n), tz: new Float32Array(n) };
    this.boot(7);
  }
  boot(seed) {
    this.sim = new Simulation({ seed, fleet: this.fleet });
    const w = this.sim.world;
    w.demandMult = this.demand;
    w.listeners.push((e) => { e.seq = ++this.seq; });
    w.log('sys', `Engine started in this browser with ${fmtInt(this.fleet)} cars per dispatcher.`);
    if (REAL) w.log('data', `Replaying real NYC taxi demand: ${REAL.source}. Hourly rate and traffic speed are measured; ${fmtInt(REAL.n)} sampled trips give the pickup and dropoff spots.`);
    for (let i = 0; i < 540; i++) this.sim.step(1);
  }
  start() { this.emit('status', 'demo'); this.emit('summary', this.summary()); }
  tick(dt) {
    if (!this.paused) { let d = Math.min(0.1, dt) * this.speed; while (d > 1e-6) { const s = Math.min(1, d); this.sim.step(s); d -= s; } }
    this.emit('frame', this.frame());
    this.acc += dt;
    if (this.acc > 0.5) { this.acc = 0; this.emit('summary', this.summary()); }
  }
  frame() {
    const d = this.view === 'smart' ? this.sim.smart : this.sim.base, t = this.sim.world.t, b = this.buf, ag = d.agents;
    for (let i = 0; i < ag.length; i++) {
      const a = ag[i];
      b.x[i] = a.x; b.z[i] = a.z;
      b.h[i] = a.hz > 0 ? 0 : a.hx > 0 ? 1 : a.hz < 0 ? 2 : 3;
      b.s[i] = a.offlineUntil >= t ? 5 : a.state === 'idle' ? (a.rebal ? 1 : 0) : a.state === 'offered' ? 2 : a.state === 'pickup' ? 3 : 4;
      if (a.rs && a.state !== 'idle') { const r = a.rs.r; b.tx[i] = a.state === 'trip' ? r.dx : r.x; b.tz[i] = a.state === 'trip' ? r.dz : r.z; }
      else { b.tx[i] = -1; b.tz[i] = -1; }
    }
    const code = { waiting: 0, offered: 1, assigned: 2 };
    return { t, clock: this.sim.world.clock(), view: this.view, n: ag.length, agents: b,
      reqs: d.active.map((rs) => [rs.r.id, rs.r.x, rs.r.z, code[rs.status] ?? 0, rs.r.p1 ? 1 : 0, t - rs.r.t]) };
  }
  timeline(id) { const log = this.sim.smart.timelines.get(id); return log ? log.map((e) => ({ ...e, clock: CFG.START_CLOCK + e.t })) : null; }
  zoneMeans(d) { return Array.from({ length: ZN }, (_, i) => (d.zN[i] ? d.zSum[i] / d.zN[i] : null)); }
  summary() {
    const s = this.sim, w = s.world, sm = s.smart;
    const idx = [...Array(ZN).keys()].sort((a, b) => sm.zForecast[b] - sm.zForecast[a]).slice(0, 5);
    const rec = sm.notable;
    return {
      t: w.t, clock: w.clock(), rate: w.rate(), batch: sm.batchNo,
      stats: { smart: sm.stats(), base: s.base.stats() },
      series: { smart: sm.m.series.map((p) => [p.t, p.wait]), base: s.base.m.series.map((p) => [p.t, p.wait]) },
      forecast: idx.map((i) => ({ zone: i, name: zoneName(i), forecast: sm.zForecast[i], need: sm.zForecast[i] * CFG.COVER, idle: sm.zIdle[i] })),
      deficit: sm.deficitZones,
      zones: { smart: this.zoneMeans(sm), base: this.zoneMeans(s.base) }, hist: { smart: Array.from(sm.hist), base: Array.from(s.base.hist) },
      fx: { outages: w.outages.map((o) => [o.x, o.z, o.r, o.id]), accidents: w.blobs.filter((b) => b.accident).map((b) => [b.x, b.z, b.r]),
        spikes: w.hotspots.filter((h) => h.spike).map((h) => [h.x, h.z, h.s * 1.6]) },
      heat: w.heat, heatScale: 1,
      notable: rec ? { ...rec, zone_name: zoneName(rec.zone), timeline: this.timeline(rec.id) } : null,
      events: w.events.slice(-40).map((e) => ({ seq: e.seq || 0, clock: e.clock, type: e.type, msg: e.msg })),
      engine: { speed: this.speed, paused: this.paused, fleet: sm.agents.length, demand: REAL ? 'real NYC TLC taxi trips' : 'synthetic city demand', eta_model: 'stand-in', forecaster: 'EWMA with trend', solver: 'greedy' },
    };
  }
  async explain(id) {
    const sm = this.sim.smart, rec = sm.explain.get(id), timeline = this.timeline(id);
    if (rec) return { found: true, rec: { ...rec, zone_name: zoneName(rec.zone), timeline } };
    const rs = sm.active.find((x) => x.r.id === id);
    return rs ? { found: false, waiting: true, id, p1: rs.r.p1, zone_name: zoneName(rs.r.zone), wait: this.sim.world.t - rs.r.t, timeline } : { found: false, waiting: false, id };
  }
  async command(c) {
    const w = this.sim.world;
    if (c.cmd === 'speed') this.speed = c.value;
    else if (c.cmd === 'pause') this.paused = c.value;
    else if (c.cmd === 'demand') { this.demand = c.value; w.demandMult = c.value; }
    else if (c.cmd === 'spike') w.triggerSpike();
    else if (c.cmd === 'outage') w.triggerOutage();
    else if (c.cmd === 'accident') w.triggerAccident();
    return { ok: true };
  }
  setView(v) { this.view = v; }
  async reset(fleet) { this.fleet = fleet; this.boot(1 + Math.floor(Math.random() * 99999)); this.emit('summary', this.summary()); }
  async models() { return window.ORCA_REPORT; }
}

class LiveSource extends Emitter {
  constructor(health) { super(); this.kind = 'live'; this.health = health; this.view = 'smart'; this.speed = health.speed; this.paused = false; }
  start() { this.connect(); }
  connect() {
    if (this.es) this.es.close();
    this.es = new EventSource(`/api/stream?view=${this.view}`);
    this.es.addEventListener('frame', (e) => { const f = JSON.parse(e.data); f.n = f.agents.x.length; this.emit('frame', f); });
    this.es.addEventListener('summary', (e) => { const s = JSON.parse(e.data); s.heatScale = 1 / (s.heat_scale || 10); this.emit('summary', s); });
    this.es.onopen = () => this.emit('status', 'live');
    this.es.onerror = () => this.emit('status', 'reconnecting');
  }
  tick() {}
  setView(v) { this.view = v; this.connect(); }
  async post(url, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (OPERATOR_TOKEN) headers.Authorization = `Bearer ${OPERATOR_TOKEN}`;
    let res;
    try {
      const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
      res = await r.json().catch(() => ({ ok: r.ok }));
      if (!r.ok && !res.error) res.error = r.status === 429 ? 'Too many requests, slow down.' : `The server refused (${r.status}).`;
    } catch (e) { res = { ok: false, error: 'The server is unreachable.' }; }
    if (!res.ok && res.error) toast(res.error);
    return res;
  }
  command(c) { if (c.cmd === 'speed') this.speed = c.value; if (c.cmd === 'pause') this.paused = c.value; return this.post('/api/command', c); }
  async explain(id) { return (await fetch(`/api/explain/${id}`)).json(); }
  reset(fleet) { return this.post('/api/reset', { fleet }); }
  async models() { try { return await (await fetch('/api/models')).json(); } catch (e) { return window.ORCA_REPORT; } }
}

// Operator token for servers in protected mode: open /app?token=... once; kept for this browser tab only.
const OPERATOR_TOKEN = (() => {
  try {
    const t = QS.get('token');
    if (t) { sessionStorage.setItem('orca-token', t); history.replaceState(null, '', location.pathname); }
    return sessionStorage.getItem('orca-token');
  } catch (e) { return null; }
})();

async function pickSource() {
  if (CAPTURE || location.hash === '#demo' || !/^https?:$/.test(location.protocol)) return new DemoSource();
  try {
    const ctl = new AbortController(); const to = setTimeout(() => ctl.abort(), 1500);
    const r = await fetch('/api/health', { signal: ctl.signal, cache: 'no-store' }); clearTimeout(to);
    if (r.ok) { const h = await r.json(); if (h && h.mode === 'live') return new LiveSource(h); }
  } catch (e) { /* no server: run the demo */ }
  return new DemoSource();
}

// ----------------------------------------------------------------------------- renderer + scene
if (!/^https?:$/.test(location.protocol)) $('#homeLink').removeAttribute('href');   // opened as a file: no home page
const canvas = $('#gl'), stage = $('.stage');
let R;
try { R = new Renderer(canvas, CAPTURE ? { shadowSize: 1536, pcf: 1 } : {}); } catch (e) {
  $('#fatal').style.display = 'grid';
  $('#fatal').textContent = 'This browser cannot start WebGL2, so the city cannot be drawn. Try a recent Chrome, Edge, Firefox or Safari.';
  throw e;
}
const DPR = CAPTURE ? 1 : Math.min(devicePixelRatio || 1, 2);
const sunDir = (() => { const v = [-0.62, 0.5, 0.48], l = Math.hypot(...v); return v.map((x) => x / l); })();
R.light = { dir: sunDir, color: [2.9, 2.72, 2.45], sky: [0.44, 0.50, 0.60], ground: [0.32, 0.30, 0.29] };
R.fog = [0.905, 0.922, 0.94]; R.skyTop = [0.80, 0.85, 0.91]; R.fogRange = [560, 1500]; R.exposure = 0.62; R.ao = 0.42;
buildCity(R);

const cars = R.mesh(carGeometry(), MAX_AGENTS, { dynamic: true, cast: true, order: 4 });
const pins = R.mesh(pinGeometry(), 1500, { dynamic: true, cast: true, order: 4 });
const rings = R.mesh(new Geo().ring(1.25, 1.95, 40, [1, 1, 1]), MAX_REQ, { dynamic: true, unlit: true, alpha: true, order: 6, depthWrite: false });
const pops = R.mesh(new Geo().ring(0.86, 1.0, 48, [1, 1, 1]), 400, { dynamic: true, unlit: true, alpha: true, order: 7, depthWrite: false });
const marker = R.mesh(new Geo().ring(3.2, 4.0, 56, [1, 1, 1]), 1, { dynamic: true, unlit: true, alpha: true, order: 8, depthWrite: false });
const jobLines = R.lines(9000, { order: 1 });
const fxLines = R.lines(6000, { order: 2, depth: false });
const discs = R.mesh(new Geo().ring(0, 1, 72, [1, 1, 1]), 24, { dynamic: true, unlit: true, alpha: true, order: 5, depthWrite: false });
const exLines = R.lines(64, { order: 3, depth: false });
const HN = 54, heatPix = new Uint8Array(HN * HN * 4), heatTex = R.heatTexture(HN);
R.planes.push({ tex: heatTex, rect: new Float32Array([0, 0, W, W]), y: 0.48, alpha: 0.9, visible: true });

const STATE_COLORS = ['#737B86', '#7FB0C8', '#FF7A00', '#FFC20A', '#2D5BE3', '#E5352B'].map(srgb);
const C_WAIT0 = srgb('#15171A'), C_WAIT1 = srgb('#E5352B'), C_OFFER = srgb('#FF7A00'), C_ASSIGNED = srgb('#C79200'), C_P1 = srgb('#E5352B');
const LINE_COL = { 2: [1.0, 0.48, 0.0, 0.9], 3: [0.69, 0.5, 0.0, 0.85], 4: [0.18, 0.36, 0.89, 0.28] };
const HEAD_ANGLE = [0, Math.PI / 2, Math.PI, -Math.PI / 2];

// ----------------------------------------------------------------------------- camera + resize
const cam = new OrbitCam([W / 2, 0, W / 2 + 6], 0.62, 1.45, 520);
cam.limits.cx = W / 2; cam.limits.cz = W / 2; cam.limits.bound = W / 2 + 60;
cam.lightCenter = [W / 2, 0, W / 2]; cam.lightRadius = 200;
const VIEWS = {
  home: { t: [W / 2, 0, W / 2 + 6], yaw: 0.62, pitch: 0.72, dist: 340 },
  top: { pitch: 1.5 },
};
let viewW = 1, viewH = 1;
function resize() {
  viewW = stage.clientWidth; viewH = stage.clientHeight;
  R.resize(viewW, viewH, DPR); cam.aspect = viewW / viewH; sizeChart();
}

// ----------------------------------------------------------------------------- live frame state + drawing
let src, curF = null, prevF = null, recvAt = 0, summary = null, view = 'smart';
let renderReqs = [];
const angle = new Float32Array(MAX_AGENTS); let angleInit = false;
const born = new Map(), lastStatus = new Map(), lastPos = new Map();
const effects = [];   // expanding rings: {x, z, t0, dur, r0, r1, col}
function addEffect(x, z, col, r1 = 4, dur = 0.8, r0 = 1) { if (effects.length < 380) effects.push({ x, z, t0: clock, dur, r0, r1, col }); }

function onFrame(f) {
  if (f.view && f.view !== view) return;
  prevF = src.kind === 'live' && curF && curF.n === f.n ? { n: curF.n, agents: { x: Float32Array.from(curF.agents.x), z: Float32Array.from(curF.agents.z) } } : null;
  curF = f; recvAt = clock;
  // request lifecycle effects
  const seen = new Set();
  for (const q of f.reqs) {
    const id = q[0]; seen.add(id);
    if (!born.has(id)) born.set(id, angleInit ? clock : -10);
    lastStatus.set(id, q[3]); lastPos.set(id, [q[1], q[2], q[4]]);
  }
  for (const [id, st] of lastStatus) if (!seen.has(id)) {
    const p = lastPos.get(id);
    if (st === 2 && p) addEffect(p[0], p[1], srgb('#C79200'), 5.5, 0.9);
    lastStatus.delete(id); lastPos.delete(id); born.delete(id);
  }
}

let clock = 0;
function drawFrame(dt) {
  const f = curF; if (!f) return;
  const A = f.agents, n = f.n, P = prevF ? prevF.agents : null;
  const alpha = P ? clamp01((clock - recvAt) / 0.105) : 1;
  const turn = 1 - Math.exp(-dt * 14);
  jobLines.reset();
  for (let i = 0; i < n; i++) {
    let x = A.x[i], z = A.z[i];
    if (P) { const px = P.x[i], pz = P.z[i]; if (Math.abs(x - px) + Math.abs(z - pz) < 8) { x = px + (x - px) * alpha; z = pz + (z - pz) * alpha; } }
    const target = HEAD_ANGLE[A.h[i]] || 0;
    if (!angleInit) angle[i] = target;
    else { let d = target - angle[i]; while (d > Math.PI) d -= Math.PI * 2; while (d < -Math.PI) d += Math.PI * 2; angle[i] += d * turn; }
    const st = A.s[i], c = STATE_COLORS[st];
    cars.set(i, x, 0, z, 1.25, 1.25, 1.25, angle[i], c[0], c[1], c[2], 1);
    if ((st === 2 || st === 3 || st === 4) && A.tx[i] >= 0) {
      const lc = LINE_COL[st], y = st === 4 ? 0.9 : 1.3;
      jobLines.seg(x, y, z, A.tx[i], y, A.tz[i], lc[0], lc[1], lc[2], lc[3], st === 4 ? 1.2 : 1.7);
    }
  }
  angleInit = true;
  cars.commit(n); jobLines.commit();

  let nr = 0, np = 0;
  renderReqs = f.reqs;
  for (const q of f.reqs) {
    if (nr >= MAX_REQ) break;
    const [id, x, z, status, p1, age] = q, w = Math.min(1, age / 600);
    const b = born.get(id) ?? -10, grow = reduceMotion ? 1 : easeOutBack(clamp01((clock - b) / 0.45));
    let s = (1 + w * 0.8) * grow * (reduceMotion ? 1 : 1 + 0.07 * Math.sin(clock * 4 + id));
    let c;
    if (status === 2) { s *= 0.55; c = C_ASSIGNED; } else if (status === 1) c = C_OFFER; else if (p1) c = C_P1;
    else c = [C_WAIT0[0] + (C_WAIT1[0] - C_WAIT0[0]) * w, C_WAIT0[1] + (C_WAIT1[1] - C_WAIT0[1]) * w, C_WAIT0[2] + (C_WAIT1[2] - C_WAIT0[2]) * w];
    rings.set(nr++, x, 0.1, z, s, 1, s, 0, c[0], c[1], c[2], 0.92);
    if (p1 && status !== 2 && np < 1500) { const k = grow; pins.set(np++, x, 0, z, k, k, k, 0, C_P1[0], C_P1[1], C_P1[2], 1); }
  }
  rings.commit(nr); pins.commit(np);

  // expanding effect rings
  let ne = 0;
  for (let i = effects.length - 1; i >= 0; i--) {
    const e = effects[i], k = (clock - e.t0) / e.dur;
    if (k >= 1) { effects.splice(i, 1); continue; }
    const r = e.r0 + (e.r1 - e.r0) * easeOut(k);
    pops.set(ne++, e.x, 0.6, e.z, r, 1, r, 0, e.col[0], e.col[1], e.col[2], (1 - k) * 0.9);
  }
  pops.commit(ne);
  $('#clock').textContent = hhmm(f.clock);
}

// ----------------------------------------------------------------------------- incidents and short zones (with shockwaves)
const knownFx = new Set();
function drawFx() {
  fxLines.reset();
  const S = summary; if (!S) { fxLines.commit(); discs.commit(0); return; }
  let nd = 0;
  const disc = (x, z, r, col) => { if (nd < 24) discs.set(nd++, x, 0.7, z, r, 1, r, 0, col[0], col[1], col[2], 0.13 + 0.05 * pulse); };
  const pulse = 0.65 + 0.35 * Math.sin(clock * 3);
  if (view === 'smart') for (const zi of S.deficit) {
    const zx = zi % CFG.ZONES, zz = Math.floor(zi / CFG.ZONES), Z = CFG.ZSIZE, p = 2.5, y = 1.2, c = [0.78, 0.57, 0.0, 0.75 * pulse];
    const x0 = zx * Z + p, x1 = (zx + 1) * Z - p, z0 = zz * Z + p, z1 = (zz + 1) * Z - p;
    for (const [a, b, cc, d] of [[x0, z0, x1, z0], [x1, z0, x1, z1], [x1, z1, x0, z1], [x0, z1, x0, z0]]) fxLines.seg(a, y, b, cc, y, d, ...c, 2.2);
  }
  const ring = (key, x, z, r, col) => {
    if (!knownFx.has(key)) { knownFx.add(key); for (let k = 0; k < 3; k++) effects.push({ x, z, t0: clock + k * 0.25, dur: 1.6, r0: 1, r1: r, col: col.slice(0, 3) }); }
    fxLines.circle(x, 1.4, z, r, col[0], col[1], col[2], 0.95 * pulse, 2.4, 96);
  };
  for (const [x, z, r] of S.fx.outages) disc(x, z, r, [0.9, 0.14, 0.1]);
  for (const [x, z, r] of S.fx.accidents) disc(x, z, r, [1.0, 0.45, 0.0]);
  for (const [x, z, r] of S.fx.spikes) disc(x, z, r, [0.42, 0.24, 0.94]);
  discs.commit(nd);
  for (const [x, z, r] of S.fx.outages) ring(`o${Math.round(x)},${Math.round(z)}`, x, z, r, [0.9, 0.14, 0.1]);
  for (const [x, z, r] of S.fx.accidents) { ring(`a${Math.round(x)},${Math.round(z)}`, x, z, r, [1.0, 0.45, 0.0]); fxLines.circle(x, 1.4, z, r * 0.45, 1, 0.45, 0, 0.9, 2.4, 48); }
  for (const [x, z, r] of S.fx.spikes) ring(`s${Math.round(x)},${Math.round(z)}`, x, z, r, [0.42, 0.24, 0.94]);
  fxLines.commit();
}
function paintHeat(arr, scale) {
  let mx = 4; for (let i = 0; i < arr.length; i++) if (arr[i] * scale > mx) mx = arr[i] * scale;
  const ST = [[0, 255, 214, 60, 0], [0.35, 255, 200, 40, 60], [0.7, 255, 130, 20, 110], [1, 229, 53, 43, 140]];
  for (let i = 0; i < HN * HN; i++) {
    const v = Math.min(1, (arr[i] * scale) / mx), o = i * 4;
    let k = 1; while (k < ST.length - 1 && v > ST[k][0]) k++;
    const a = ST[k - 1], b = ST[k], f = clamp01((v - a[0]) / (b[0] - a[0]));
    for (let c = 0; c < 4; c++) heatPix[o + c] = a[c + 1] + (b[c + 1] - a[c + 1]) * f;
  }
  heatTex.update(heatPix);
}

// ----------------------------------------------------------------------------- the explained decision, drawn into the city
const plates = Array.from({ length: 3 }, () => { const d = document.createElement('div'); d.className = 'plate'; $('#tags').appendChild(d); return d; });
let shown = null, shownAt = 0;
function show3D(rec) { shown = rec; shownAt = clock; }
function drawExplain() {
  exLines.reset();
  if (!shown) { exLines.commit(); marker.commit(0); plates.forEach((p) => p.classList.remove('on')); return; }
  const r = shown, k = reduceMotion ? 1 : easeOut(clamp01((clock - shownAt) / 0.8));
  const seg = (ax, az, col, w, y) => exLines.seg(ax, y, az, ax + (r.x - ax) * k, y, az + (r.z - az) * k, ...col, w);
  for (const c of r.cands) if (!c.chosen) seg(c.rx, c.rz, [0.55, 0.58, 0.62, 0.7], 1.6, 2.2);
  if (r.base && !r.base.same) seg(r.base.rx, r.base.rz, [0.33, 0.35, 0.39, 1], 3, 2.4);
  seg(r.chosen.rx, r.chosen.rz, [0.88, 0.66, 0.0, 1], 4.2, 2.6);
  exLines.commit();
  const m = 1 + (reduceMotion ? 0 : 0.08 * Math.sin(clock * 3));
  marker.set(0, r.x, 0.2, r.z, m, 1, m, 0, 0, 0, 0, 0.9); marker.commit(1);
  plate(plates[0], r.x, 10, r.z, `Request ${r.id}`, '');
  plate(plates[1], r.chosen.rx, 4, r.chosen.rz, `ORCA picks ${car(r.chosen.id)}`, 'pick');
  if (r.base && !r.base.same) plate(plates[2], r.base.rx, 4, r.base.rz, `Nearest: ${car(r.base.id)}`, 'near'); else plates[2].classList.remove('on');
}
function plate(el, x, y, z, text, cls) {
  const [sx, sy, vis] = cam.project(x, y, z, viewW, viewH);
  if (!vis || sx < -40 || sy < -40 || sx > viewW + 40 || sy > viewH + 40 || clock - shownAt < 0.5) { el.classList.remove('on'); return; }
  el.className = 'plate on ' + cls; el.textContent = text; el.style.left = sx + 'px'; el.style.top = sy + 'px';
}

// ----------------------------------------------------------------------------- input: camera, picking, custom cursor
const cursor = $('#cursor'), cursorLbl = $('#cursorLbl');
let ptr = { x: 0, y: 0, cx: 0, cy: 0, inside: false }, drag = null, hoverReq = null;
const finePointer = matchMedia('(hover: hover) and (pointer: fine)').matches;
function nearestReq(sx, sy, radius = 24) {
  let best = null, bd = radius * radius;
  for (const q of renderReqs) {
    const [x, y, vis] = cam.project(q[1], 0.1, q[2], viewW, viewH); if (!vis) continue;
    const d = (x - sx) ** 2 + (y - sy) ** 2; if (d < bd) { bd = d; best = q; }
  }
  return best;
}
function local(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
canvas.addEventListener('contextmenu', (e) => e.preventDefault());
canvas.addEventListener('pointerdown', (e) => {
  canvas.setPointerCapture(e.pointerId); stopTourOnInput();
  const [x, y] = local(e);
  const mode = e.button === 2 || e.shiftKey || e.button === 1 ? 'pan' : 'rotate';
  drag = { mode, x, y, sx: x, sy: y, moved: 0, grab: mode === 'pan' ? cam.ground(x, y, viewW, viewH) : null };
});
canvas.addEventListener('pointermove', (e) => {
  const [x, y] = local(e);
  ptr.x = e.clientX; ptr.y = e.clientY; ptr.inside = true;
  if (!drag) { hoverReq = finePointer ? nearestReq(x, y) : null; return; }
  const dx = x - drag.x, dy = y - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy); drag.x = x; drag.y = y;
  if (drag.mode === 'rotate') cam.rotate(dx, dy);
  else if (drag.grab) {    // keep the grabbed ground point under the pointer
    const g = cam.ground(x, y, viewW, viewH);
    if (g) { cam.tween = null; cam.g.t[0] += drag.grab[0] - g[0]; cam.g.t[2] += drag.grab[2] - g[2]; cam.t[0] += drag.grab[0] - g[0]; cam.t[2] += drag.grab[2] - g[2]; cam.clampGoal(); cam.update(0); }
  }
});
canvas.addEventListener('pointerup', (e) => {
  const d = drag; drag = null;
  if (d && d.moved < 6) {
    const [x, y] = local(e), q = nearestReq(x, y);
    if (q) { pinned = q[0]; selectTab('why'); refreshPinned(); addEffect(q[1], q[2], [0, 0, 0], 4, 0.5); }
  }
});
canvas.addEventListener('pointerleave', () => { ptr.inside = false; hoverReq = null; });
canvas.addEventListener('dblclick', (e) => {
  const [x, y] = local(e), g = cam.ground(x, y, viewW, viewH);
  if (g) cam.flyTo({ t: [g[0], 0, g[2]], dist: Math.max(70, cam.dist * 0.5) }, 1.1);
});
canvas.addEventListener('wheel', (e) => {
  e.preventDefault(); stopTourOnInput();
  const [x, y] = local(e), g = cam.ground(x, y, viewW, viewH);
  cam.zoom(Math.exp(e.deltaY * (e.deltaMode ? 0.05 : 0.0012)), g);
}, { passive: false });
// two-finger pinch / pan on touch screens
const touches = new Map();
canvas.addEventListener('touchstart', (e) => { for (const t of e.changedTouches) touches.set(t.identifier, [t.clientX, t.clientY]); }, { passive: true });
canvas.addEventListener('touchmove', (e) => {
  if (touches.size !== 2 || e.touches.length !== 2) return;
  drag = null;
  const [a, b] = [...e.touches], pa = touches.get(a.identifier), pb = touches.get(b.identifier);
  if (!pa || !pb) return;
  const d0 = Math.hypot(pa[0] - pb[0], pa[1] - pb[1]), d1 = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  cam.zoom(d0 / Math.max(1, d1));
  cam.pan(((a.clientX + b.clientX) - (pa[0] + pb[0])) / 2, ((a.clientY + b.clientY) - (pa[1] + pb[1])) / 2);
  touches.set(a.identifier, [a.clientX, a.clientY]); touches.set(b.identifier, [b.clientX, b.clientY]);
}, { passive: true });
canvas.addEventListener('touchend', (e) => { for (const t of e.changedTouches) touches.delete(t.identifier); }, { passive: true });

const keys = new Set();
addEventListener('keydown', (e) => {
  if (e.target.closest('input, dialog')) return;
  if (e.key === ' ') { e.preventDefault(); if (touring) tourTogglePlay(); else togglePause(); return; }
  if (e.key === 'Escape') { if (touring) endTour(); else { pinned = null; lastSwap = 0; if (summary) renderWhy(summary.notable); } return; }
  if (e.target !== document.body && e.target !== canvas) return;
  const k = e.key.toLowerCase();
  if ('wasdqerf+-='.includes(k) || k.startsWith('arrow')) { keys.add(k); e.preventDefault(); }
  if (k === 'h') goHome();
});
addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
function keyboardCam(dt) {
  if (!keys.size) return;
  const sp = cam.dist * 0.9 * dt, c = Math.cos(cam.yaw), s = Math.sin(cam.yaw);
  let f = 0, r = 0;
  if (keys.has('w') || keys.has('arrowup')) f += 1; if (keys.has('s') || keys.has('arrowdown')) f -= 1;
  if (keys.has('d') || keys.has('arrowright')) r += 1; if (keys.has('a') || keys.has('arrowleft')) r -= 1;
  if (f || r) cam.panWorld((r * c - f * s) * sp, (-r * s - f * c) * sp);
  if (keys.has('q')) cam.rotate(-260 * dt, 0); if (keys.has('e')) cam.rotate(260 * dt, 0);
  if (keys.has('r')) cam.rotate(0, 200 * dt); if (keys.has('f')) cam.rotate(0, -200 * dt);
  if (keys.has('+') || keys.has('=')) cam.zoom(1 - dt * 1.4); if (keys.has('-')) cam.zoom(1 + dt * 1.4);
}
function goHome(dur = 1.4) { cam.flyTo(VIEWS.home, dur); }
$('#home').onclick = () => goHome();
$('#zin').onclick = () => cam.zoom(0.7);
$('#zout').onclick = () => cam.zoom(1.4);
$('#tilt').onclick = () => cam.flyTo({ pitch: cam.g.pitch > 1.2 ? 0.72 : 1.5 }, 0.9);
$('#compass').onclick = () => cam.flyTo({ yaw: 0 }, 0.9);
function flyToRequest(x, z, dist = 130) { cam.flyTo({ t: [x, 0, z], dist, pitch: Math.min(cam.g.pitch, 0.9) }, 1.4); }

function updateCursor() {
  if (!finePointer || CAPTURE) return;
  const on = ptr.inside || !!drag;
  cursor.classList.toggle('show', on);
  if (!on) return;
  ptr.cx += (ptr.x - ptr.cx) * 0.45; ptr.cy += (ptr.y - ptr.cy) * 0.45;
  cursor.style.transform = `translate(${ptr.cx}px, ${ptr.cy}px)`;
  const state = drag ? (drag.mode === 'pan' ? 'pan' : drag.moved > 6 ? 'drag' : '') : hoverReq ? 'hover' : '';
  cursor.className = 'show ' + state + (state ? ' labeled' : '');
  cursorLbl.className = 'lbl' + (state === 'hover' ? ' yellow' : '');
  cursorLbl.textContent = state === 'hover' ? `Inspect request ${hoverReq[0]}${hoverReq[4] ? ', urgent' : ''}` : state === 'drag' ? 'Rotate' : state === 'pan' ? 'Move' : '';
}
setInterval(() => { const deg = (-cam.yaw * 180) / Math.PI; $('#compass svg').style.transform = `rotate(${deg}deg)`; }, 50);

// ----------------------------------------------------------------------------- console: scoreboard (tweened), grid, chart
const GRID_ROWS = [
  ['95th percentile wait', 'p95', mmss], ['Urgent request wait', 'p1', mmss], ['Riders picked up', 'served', fmtInt],
  ['Riders who gave up', 'lost', fmtInt], ['Offers declined or expired', 'reoffers', fmtInt], ['Cars busy', 'util', (v) => Math.round(v * 100) + '%'],
];
$('#grid').innerHTML = `<span class="h"></span><span class="h" style="text-align:right">ORCA</span><span class="h" style="text-align:right">Nearest</span>` +
  GRID_ROWS.map((r, i) => `<span class="lbl">${r[0]}</span><span class="v" id="g${i}a">–</span><span class="v n" id="g${i}b">–</span>`).join('');
const tw = { a: null, b: null, ta: 0, tb: 0 };
function renderBoard(S) {
  const a = S.stats.smart, b = S.stats.base, mx = Math.max(a.mean, b.mean, 1);
  tw.ta = a.mean; tw.tb = b.mean; if (tw.a == null) { tw.a = a.mean; tw.b = b.mean; }
  $('#b-orca').style.width = (a.mean / mx) * 100 + '%'; $('#b-near').style.width = (b.mean / mx) * 100 + '%';
  const gain = b.mean ? (b.mean - a.mean) / b.mean : 0;
  $('#verdict').innerHTML = gain >= 0.02 ? `On the same requests, ORCA riders wait <b>${Math.round(gain * 100)}% less</b> than with nearest-car dispatch.`
    : gain <= -0.02 ? `Nearest-car dispatch is ahead right now, by ${Math.round(-gain * 100)}%.` : 'Both dispatchers are performing about the same right now.';
  GRID_ROWS.forEach((r, i) => { $(`#g${i}a`).textContent = r[2](a[r[1]]); $(`#g${i}b`).textContent = r[2](b[r[1]]); });
}
function tweenBoard(dt) {
  if (tw.a == null) return;
  const k = 1 - Math.exp(-dt * 5);
  tw.a += (tw.ta - tw.a) * k; tw.b += (tw.tb - tw.b) * k;
  $('#w-orca').textContent = mmss(tw.a); $('#w-near').textContent = mmss(tw.b);
}
const chart = $('#chart'), cg = chart.getContext('2d');
function sizeChart() { const r = chart.getBoundingClientRect(); const d = Math.min(devicePixelRatio || 1, 2); chart.width = Math.max(1, r.width * d); chart.height = Math.max(1, r.height * d); chart.dpr = d; if (summary) drawChart(summary); }
function drawChart(S) {
  const d = chart.dpr || 1, w = chart.width, h = chart.height, g = cg; g.clearRect(0, 0, w, h);
  const sa = S.series.smart, sb = S.series.base, n = Math.max(sa.length, sb.length);
  if (n < 2) return;
  let mx = 60; for (const s of [sa, sb]) for (const p of s) if (p[1] != null && p[1] > mx) mx = p[1];
  mx = Math.ceil(mx / 60) * 60;
  const T = 6 * d, B = 16 * d, ph = h - T - B, X = (i) => (i / (n - 1)) * w, Y = (v) => T + ph - (v / mx) * ph;
  g.strokeStyle = '#DADDE1'; g.lineWidth = 1; g.beginPath(); g.moveTo(0, T + ph + 0.5); g.lineTo(w, T + ph + 0.5); g.stroke();
  g.font = `${11.5 * d}px Archivo, sans-serif`; g.fillStyle = '#71767E'; g.textBaseline = 'top';
  g.fillText(`${Math.round(mx / 60)} min`, 0, 0); g.textAlign = 'right'; g.fillText(`last ${n} min`, w, T + ph + 4 * d); g.textAlign = 'left';
  const path = (s) => { const pts = []; s.forEach((p, i) => { if (p[1] != null) pts.push([X(i + n - s.length), Y(p[1])]); }); return pts; };
  const pa = path(sa), pb = path(sb);
  if (pa.length > 1) { g.beginPath(); g.moveTo(pa[0][0], T + ph); for (const [x, y] of pa) g.lineTo(x, y); g.lineTo(pa[pa.length - 1][0], T + ph); g.closePath(); g.fillStyle = 'rgba(255,194,10,.32)'; g.fill(); }
  const stroke = (pts, c, lw) => { if (pts.length < 2) return; g.beginPath(); pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.strokeStyle = c; g.lineWidth = lw * d; g.lineJoin = 'round'; g.stroke(); };
  stroke(pb, '#8B929B', 1.5); stroke(pa, '#000', 2);
}

// ----------------------------------------------------------------------------- console: tabs
const TABS = ['why', 'fc', 'zn', 'ev']; let tab = 'why';
function selectTab(t) { tab = t; for (const k of TABS) { $('#t-' + k).setAttribute('aria-selected', k === t); $('#p-' + k).hidden = k !== t; } }
for (const k of TABS) $('#t-' + k).onclick = () => selectTab(k);
$('.tabs').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  const n = TABS[(TABS.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length]; selectTab(n); $('#t-' + n).focus();
});

// ---- why this car
let pinned = null, lastSwap = -99, shownId = null;
const TL = { request: ['Rider requested', ''], offer: ['Offered to', ''], accept: ['Accepted by', 'ok'], decline: ['Declined by', 'bad'], expire: ['No answer from', 'bad'], pickup: ['Picked up by', 'done'], cancel: ['Rider gave up', 'bad'] };
function timelineHTML(tl) {
  if (!tl || !tl.length) return '';
  return `<ol class="tl num">${tl.map((e) => { const [label, cls] = TL[e.k || e.kind] || [e.k, '']; const who = e.car != null ? ` ${car(e.car)}` : '';
    return `<li class="${cls}"><span>${label}${who}</span><time>${hhmm(e.clock ?? CFG.START_CLOCK + e.t)}</time></li>`; }).join('')}</ol>`;
}
function renderWhy(rec) {
  const el = $('#p-why');
  if (!rec) { el.innerHTML = '<p class="muted">Waiting for ORCA\'s first interesting decision.</p>'; show3D(null); return; }
  shownId = rec.id; show3D(rec);
  const near = rec.base && !rec.base.same ? rec.base : null;
  const top = rec.cands.slice(0, 4); if (!top.some((c) => c.chosen)) top.push(rec.cands.find((c) => c.chosen));
  const nearIn = near && top.some((c) => c.id === near.id);
  const rows = [...top, ...(near && !nearIn ? [{ ...near, chosen: false }] : [])].map((c) => {
    const isNear = near && c.id === near.id, stale = c.age > CFG.STALE_AFTER;
    const score = stale ? '<span class="bad">no GPS</span>' : c.taken ? '<span class="dim">used elsewhere</span>' : Math.round(c.cost);
    return `<tr class="${c.chosen ? 'pick' : isNear ? 'near' : ''}"><td>${car(c.id)}</td><td>${mmss(c.eta)}</td><td>${Math.round(c.p * 100)}%</td><td class="${stale ? 'bad' : ''}">${Math.round(c.age)}s</td><td>${score}</td></tr>`;
  }).join('');
  el.innerHTML = `
    <div class="ex-head"><span class="badge ${rec.p1 ? 'p1' : ''}">${rec.p1 ? 'Urgent' : 'Standard'}</span><span>Request ${rec.id} in zone ${rec.zone_name}</span><span>${hhmm(rec.clock)}</span></div>
    <p class="ex-title">Sent to <mark>${car(rec.chosen.id)}</mark>, about ${mmss(rec.chosen.eta)} away by road.</p>
    <table class="cands num"><thead><tr><th>Candidate</th><th>Drive</th><th>Accepts</th><th>GPS age</th><th>Score</th></tr></thead><tbody>${rows}</tbody></table>
    ${rec.base ? (rec.base.same ? `<p class="why same"><strong>Same car either way.</strong> Nearest-car dispatch would have picked it too.</p>`
      : `<p class="why"><strong>Why not ${car(rec.base.id)}?</strong> It is the car nearest-car dispatch would send. ${rec.base.reason}</p>`) : ''}
    ${timelineHTML(rec.timeline)}
    <div class="ex-actions"><button class="btn" id="fly">Zoom to this request</button><button class="btn" id="whole">Whole city</button>${pinned != null ? '<button class="btn" id="unpin">Follow live decisions</button>' : ''}</div>
    <p class="formula">Score is drive time, plus ${CFG.REOFFER} seconds times the chance the driver says no, plus a cost for taking the last spare car from a zone about to get busy. Lowest score wins, solved for the whole ${CFG.BATCH}-second batch at once.</p>`;
  $('#fly').onclick = () => flyToRequest(rec.x, rec.z);
  $('#whole').onclick = () => goHome();
  const u = $('#unpin'); if (u) u.onclick = unpin;
}
function unpin() { pinned = null; lastSwap = -99; if (summary) renderWhy(summary.notable); }
async function refreshPinned() {
  if (pinned == null) return;
  const res = await src.explain(pinned); if (pinned == null) return;
  if (res.found) { renderWhy(res.rec); return; }
  show3D(null);
  $('#p-why').innerHTML = res.waiting
    ? `<div class="ex-head"><span class="badge ${res.p1 ? 'p1' : ''}">${res.p1 ? 'Urgent' : 'Standard'}</span><span>Request ${res.id} in zone ${res.zone_name}</span></div>
       <p class="ex-title">Waiting for the next matching round.</p><p class="muted">This rider has waited ${mmss(res.wait)}. ORCA matches every ${CFG.BATCH} seconds, so this updates on its own.</p>
       ${timelineHTML(res.timeline)}<div class="ex-actions"><button class="btn" id="unpin">Follow live decisions</button></div>`
    : `<p class="muted">Request ${res.id} was handled too long ago to explain. Click a ring that is still on the map.</p><div class="ex-actions"><button class="btn" id="unpin">Follow live decisions</button></div>`;
  $('#unpin').onclick = unpin;
}

// ---- demand, zones, events
function renderForecast(S) {
  const mx = Math.max(1, ...S.forecast.map((f) => Math.max(f.forecast, f.idle)));
  $('#p-fc').innerHTML = `<p class="muted" style="margin:0 0 12px">The busiest zones over the next 5 minutes, as forecast by ORCA, against idle cars there now. Zones outlined in dark yellow on the map are where ORCA is moving spare cars in ahead of the demand.</p>
    <div class="fc-row h"><span>Zone</span><span></span><span>Rides</span><span>Idle cars</span></div>` +
    S.forecast.map((f) => `<div class="fc-row num"><span class="z">${f.name}</span><span class="fc-track"><b style="width:${(f.forecast / mx) * 100}%"></b><i style="width:${Math.min(100, (f.idle / mx) * 100)}%"></i></span>
      <span class="n">${Math.round(f.forecast)}</span><span class="n idle">${f.idle}</span></div>`).join('');
}
const ZCOL = [[244, 241, 230], [255, 216, 77], [255, 154, 46], [229, 53, 43]];
function zoneColor(v, mx) {
  if (v == null) return '#E9EBEE';
  const t = Math.min(1, v / mx) * 3, i = Math.min(2, Math.floor(t)), f = t - i, a = ZCOL[i], b = ZCOL[i + 1];
  return `rgb(${a.map((c, k) => Math.round(c + (b[k] - c) * f)).join(',')})`;
}
let zonesBuilt = false;
function renderZones(S) {
  if (!S.zones) { $('#p-zn').innerHTML = '<p class="muted">Zone statistics will appear after the first pickups.</p>'; return; }
  if (!zonesBuilt) {
    const cells = (id) => `<div class="zmap" id="${id}">${Array.from({ length: ZN }, (_, i) => `<span data-z="${i}"></span>`).join('')}</div>`;
    $('#p-zn').innerHTML = `<p class="muted" style="margin:0 0 12px">Average wait in each of the 49 zones since the city started, on the same colour scale. North is at the top.</p>
      <div class="zgrids"><div><h4>ORCA</h4>${cells('zm-a')}</div><div><h4>Nearest car</h4>${cells('zm-b')}</div></div>
      <div class="zscale"><span>0:00</span><i></i><span id="zmax">5:00</span></div>
      <div class="hist"><h4>How long riders waited</h4><div class="hrow" id="hrow"></div><div class="hax"><span>0 min</span><span>3 min</span><span>6+ min</span></div>
      <p class="hint">Black: ORCA. Grey: nearest car. Each pair is a 30-second band.</p></div>`;
    zonesBuilt = true;
  }
  const all = [...S.zones.smart, ...S.zones.base].filter((v) => v != null), mx = Math.max(120, ...all);
  $('#zmax').textContent = mmss(mx);
  for (const [id, arr, other, name] of [['#zm-a', S.zones.smart, S.zones.base, 'ORCA'], ['#zm-b', S.zones.base, S.zones.smart, 'Nearest']]) {
    $(id).querySelectorAll('span').forEach((c, i) => { c.style.background = zoneColor(arr[i], mx); c.title = `${zoneName(i)}: ${arr[i] == null ? 'no pickups yet' : mmss(arr[i])} with ${name}`; });
  }
  const ha = S.hist.smart, hb = S.hist.base, ta = ha.reduce((a, b) => a + b, 0) || 1, tb = hb.reduce((a, b) => a + b, 0) || 1;
  const mh = Math.max(...ha.map((v) => v / ta), ...hb.map((v) => v / tb), 0.01);
  $('#hrow').innerHTML = ha.map((v, i) => `<div><b style="height:${((v / ta) / mh) * 100}%"></b><b class="n" style="height:${((hb[i] / tb) / mh) * 100}%"></b></div>`).join('');
}
let lastSeq = -1;
function renderEvents(S) {
  const el = $('#p-ev'), fresh = S.events.filter((e) => e.seq > lastSeq);
  if (!fresh.length) return;
  if (lastSeq < 0) el.innerHTML = '';
  for (const e of fresh) {
    const d = document.createElement('div'); d.className = 'ev ' + e.type;
    d.innerHTML = `<time class="num">${hhmm(e.clock)}</time><span></span>`; d.querySelector('span').textContent = e.msg; el.prepend(d);
    if (lastSeq >= 0 && !touring && ['spike', 'outage', 'accident'].includes(e.type)) toast(e.msg);
  }
  while (el.children.length > 60) el.lastChild.remove();
  lastSeq = Math.max(lastSeq, ...fresh.map((e) => e.seq));
}
function renderEngine(S) {
  const e = S.engine;
  $('#engine').textContent = src.kind === 'live'
    ? `Connected to the ORCA server, ${fmtInt(e.fleet)} cars per dispatcher, demand from ${e.demand}. ETA model: ${e.eta_model}. Forecast: ${e.forecaster}. Matching: ${e.solver}${e.solve_ms != null ? `, ${e.solve_ms} ms per batch` : ''}.`
    : `Running in this browser, ${fmtInt(e.fleet)} cars per dispatcher, demand from ${e.demand}${REAL ? ' (a 24,000-trip sample at the real hourly rate)' : ''}. Start the Python server for the trained ETA and demand models and the full 2-million-trip replay.`;
}
function onSummary(S) {
  summary = S;
  renderBoard(S); drawChart(S); renderForecast(S); renderZones(S); renderEvents(S); renderEngine(S);
  paintHeat(S.heat, S.heatScale ?? 1);
  if (touring) return;
  if (pinned != null) refreshPinned();
  else if (S.notable && S.notable.id !== shownId && clock - lastSwap > 6) { lastSwap = clock; renderWhy(S.notable); }
  else if (!S.notable && shownId == null) renderWhy(null);
}

// ----------------------------------------------------------------------------- model report
$('#repBtn').onclick = async () => {
  const d = await src.models(), dlg = $('#report');
  const table = (rows, unit) => {
    if (!rows) return '<p class="muted">Not trained yet. Run <code>python -m orca.train</code>.</p>';
    const best = Math.min(...rows.map((r) => r.mae));
    return `<table class="num"><thead><tr><th>Model</th><th>Mean error</th><th>90% of errors under</th><th>R²</th></tr></thead><tbody>${rows.map((r) =>
      `<tr class="${r.mae === best ? 'best' : ''}"><td>${r.model}</td><td>${r.mae.toFixed(2)}${unit}</td><td>${r.p90_abs_err.toFixed(1)}${unit}</td><td>${r.r2.toFixed(3)}</td></tr>`).join('')}</tbody></table>`;
  };
  const bench = d && d.benchmark ? (() => {
    const pols = [['nearest', 'Nearest car'], ['orca_heuristic', 'ORCA, heuristic models'], ['orca_learned', 'ORCA, learned models']];
    const agg = (scen, pol, k) => { const rs = d.benchmark.rows.filter((r) => r.scenario === scen); return rs.reduce((a, r) => a + r[pol][k], 0) / rs.length; };
    return ['steady', 'stress'].map((sc) => `<h3>${sc === 'steady' ? 'Steady demand' : 'Spike, GPS outage and accident'}</h3><table class="num"><thead><tr><th>Dispatcher</th><th>Mean wait</th><th>95th pct.</th><th>Gave up</th></tr></thead><tbody>${pols.map(([p, l]) =>
      `<tr class="${p === 'orca_learned' ? 'best' : ''}"><td>${l}</td><td>${mmss(agg(sc, p, 'mean'))}</td><td>${mmss(agg(sc, p, 'p95'))}</td><td>${fmtInt(agg(sc, p, 'lost'))}</td></tr>`).join('')}</tbody></table>`).join('');
  })() : '';
  $('#repBody').innerHTML = `<button class="x" id="repX" aria-label="Close">×</button>
    <h2>How the models perform</h2>
    <p>Every learned model is tested on data from later in time than it was trained on, against the simpler method it replaces. Numbers come from the training run that produced the models this server uses.</p>
    <h3>Pickup drive time (ETA), ${d && d.eta ? fmtInt(d.eta.n_test) + ' held-out pickups' : ''}</h3>${table(d && d.eta && d.eta.results, 's')}
    <h3>Rides per zone in the next 5 minutes</h3>${table(d && d.demand && d.demand.results, '')}
    ${bench ? `<h3 style="margin-top:28px;font-size:18px">Head-to-head benchmark</h3><p style="margin:0;color:var(--ink-2);font-size:14px">${d.benchmark.config.minutes} simulated minutes per run, ${fmtInt(d.benchmark.config.fleet)} cars, identical requests and incidents for every dispatcher.</p>${bench}` : ''}`;
  $('#repX').onclick = () => dlg.close();
  dlg.showModal();
};
$('#report').addEventListener('click', (e) => { if (e.target.id === 'report') e.target.close(); });

// ----------------------------------------------------------------------------- controls
let toastT;
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('on'), 3600); }
function setView(v) {
  view = v; src.setView(v); prevF = null; angleInit = false; born.clear(); lastStatus.clear(); lastPos.clear();
  document.querySelectorAll('[data-view]').forEach((b) => b.setAttribute('aria-pressed', b.dataset.view === v));
}
document.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => setView(b.dataset.view)));
document.querySelectorAll('[data-cmd]').forEach((b) => (b.onclick = () => src.command({ cmd: b.dataset.cmd })));
function setSpeed(v) { src.command({ cmd: 'speed', value: v }); document.querySelectorAll('[data-speed]').forEach((x) => x.setAttribute('aria-pressed', +x.dataset.speed === v)); }
document.querySelectorAll('[data-speed]').forEach((b) => (b.onclick = () => setSpeed(+b.dataset.speed)));
const PAUSE_SVG = '<svg viewBox="0 0 14 14" fill="currentColor"><rect x="2" y="1.5" width="3.4" height="11" rx=".6"/><rect x="8.6" y="1.5" width="3.4" height="11" rx=".6"/></svg>';
const PLAY_SVG = '<svg viewBox="0 0 14 14" fill="currentColor"><path d="M3 1.6v10.8L12.2 7z"/></svg>';
let paused = false;
$('#pause').innerHTML = PAUSE_SVG;
function togglePause() { paused = !paused; src.command({ cmd: 'pause', value: paused }); $('#pause').innerHTML = paused ? PLAY_SVG : PAUSE_SVG; $('#pause').setAttribute('aria-label', paused ? 'Resume' : 'Pause'); }
$('#pause').onclick = togglePause;
$('#demand').oninput = (e) => { $('#demandV').textContent = (+e.target.value).toFixed(2) + '×'; };
$('#demand').onchange = (e) => src.command({ cmd: 'demand', value: +e.target.value });
$('#fleet').oninput = (e) => { $('#fleetV').textContent = fmtInt(+e.target.value); };
$('#fleet').onchange = async (e) => {
  const n = +e.target.value; toast(`Restarting the city with ${fmtInt(n)} cars per dispatcher…`);
  await src.reset(n); lastSeq = -1; pinned = null; shownId = null; prevF = curF = null; angleInit = false; born.clear(); lastStatus.clear(); knownFx.clear();
};
$('#mode').onclick = () => toast(src.kind === 'live' ? 'Live: streaming from the Python ORCA server with the trained ETA and demand models.'
  : 'In-browser engine: the dispatch engine runs inside this page on a sample of real NYC taxi trips. Start the Python server to stream from the full backend.');
function closeWelcome() { $('#welcome').hidden = true; try { sessionStorage.setItem('orca-welcome', '1'); } catch (e) { /* ignore */ } }
try { if (sessionStorage.getItem('orca-welcome') === '1') $('#welcome').hidden = true; } catch (e) { /* storage unavailable */ }
$('#w-ok').onclick = closeWelcome;
$('#w-tour').onclick = () => { closeWelcome(); startTour(); };
$('#tourBtn').onclick = () => (touring ? endTour() : startTour());
function setStatus(s) { $('#mode').classList.toggle('live', s === 'live'); $('#modeTxt').textContent = s === 'live' ? 'Live server' : s === 'reconnecting' ? 'Reconnecting…' : 'In-browser engine'; }

// ----------------------------------------------------------------------------- guided tour (plays like a video)
// Each chapter: duration, caption, camera move, and what the app does on entry.
const TOUR = [
  { dur: 8, t: 'This is ORCA', b: REAL ? 'A live city of 1,200 cars, with ride requests replayed from real New York taxi trips. For each one, a dispatcher must decide which car goes. ORCA makes that decision.' : 'A live city of 1,200 cars. Every few seconds someone asks for a ride, and a dispatcher has to decide which car goes. ORCA makes that decision.',
    cam: { t: [W / 2, 0, W / 2 + 6], yaw: 0.35, pitch: 0.95, dist: 420 }, orbit: 0.035 },
  { dur: 9, t: 'Two dispatchers, one city', b: 'Every request goes to two dispatchers at once. The usual rule sends the nearest car. ORCA decides differently. They share the same requests, traffic and drivers, so any difference comes from the decisions alone.',
    cam: { t: [W / 2, 0, W / 2], yaw: 0.7, pitch: 0.78, dist: 330 }, spot: '#board', orbit: 0.02 },
  { dur: 9, t: 'Reading the map', b: 'Yellow cars are on their way to a pickup. Blue cars have a rider on board. Grey cars are free. Rings are riders waiting, and red pins mark urgent requests.',
    cam: { t: [W * 0.7, 0, W * 0.64], yaw: 0.95, pitch: 0.74, dist: 105 }, spot: '.key', orbit: 0.03 },
  { dur: 12, t: 'One decision, up close', b: 'ORCA just assigned this request. It scored ten nearby cars on drive time by road, the chance each driver accepts, how fresh its GPS is, and whether taking it leaves a busy zone short.',
    do: 'decision', spot: '#p-why' },
  { dur: 10, t: 'Why not the nearest car?', b: '', do: 'nearest', spot: '#p-why' },
  { dur: 9, t: 'Every 3 seconds, the best match for everyone', b: 'ORCA gathers requests for three seconds and solves them together with the Hungarian algorithm, so one rider\'s best car is not taken by someone who has other good options.',
    cam: { t: [W / 2, 0, W / 2], yaw: 1.2, pitch: 0.9, dist: 300 }, orbit: 0.03 },
  { dur: 12, t: 'Breaking the city', b: 'A network outage hits. Cars inside the red circle stop reporting GPS. The nearest-car rule keeps offering rides to cars that cannot answer. ORCA notices the silence and works around it.',
    do: 'outage' },
  { dur: 12, t: 'Seeing demand before it arrives', b: 'Now a demand spike begins. A gradient-boosted model forecasts rides per zone for the next five minutes, and ORCA moves spare cars into zones about to run short, outlined in yellow.',
    do: 'spike', spot: '#p-fc' },
  { dur: 9, t: 'The result', b: '', do: 'result', spot: '#board', cam: { t: [W / 2, 0, W / 2], yaw: 0.6, pitch: 0.85, dist: 360 }, orbit: 0.02 },
  { dur: 10, t: 'How it is built', b: 'A Python engine with scikit-learn models and SciPy matching streams to this page over Server-Sent Events, and a hand-written WebGL2 renderer draws the city. Now try it: switch dispatchers, cause incidents, click any ring.',
    cam: { t: [W / 2, 0, W / 2 + 6], yaw: 0.62, pitch: 0.72, dist: 340 }, orbit: 0.03 },
];
const TOUR_TOTAL = TOUR.reduce((a, c) => a + c.dur, 0);
let touring = false, tourT = 0, tourCh = -1, tourPlaying = false, voice = !CAPTURE, music = !CAPTURE, tourSaved = null;
// Original score for the tour (tools/make_tour_video.py), kept in sync with the tour clock.
const SCORE = new Audio(/^https?:$/.test(location.protocol) ? '/media/orca-tour-score.mp3' : '../docs/orca-tour-score.mp3');
SCORE.preload = 'none'; SCORE.volume = 0.5; let scorePending = false;
function syncMusic() {
  if (!music || !touring || !tourPlaying || CAPTURE) { if (!SCORE.paused) SCORE.pause(); return; }
  if (SCORE.readyState >= 2 && !SCORE.seeking && Math.abs(SCORE.currentTime - tourT) > 0.4) SCORE.currentTime = tourT;
  if (SCORE.paused && !scorePending) {
    scorePending = true;
    SCORE.play().then(() => { SCORE.currentTime = tourT; })
      .catch((e) => { if (e.name !== 'AbortError') { music = false; $('#tMusic').setAttribute('aria-pressed', false); } })
      .finally(() => { scorePending = false; });
  }
}
$('#tVoice').setAttribute('aria-pressed', voice); $('#tMusic').setAttribute('aria-pressed', music);
$('#tMusic').onclick = () => { music = !music; $('#tMusic').setAttribute('aria-pressed', music); syncMusic(); };
$('#tTrack').innerHTML = TOUR.map((c, i) => `<div class="sg" data-i="${i}" style="flex:${c.dur}" title="${c.t}"><b></b></div>`).join('');
$('#tTrack').addEventListener('click', (e) => { const s = e.target.closest('.sg'); if (s) seekChapter(+s.dataset.i); });
$('#tPlay').onclick = () => tourTogglePlay();
$('#tClose').onclick = () => endTour();
$('#tVoice').onclick = () => { voice = !voice; $('#tVoice').setAttribute('aria-pressed', voice); if (!voice) window.speechSynthesis?.cancel(); else if (tourCh >= 0) speak(currentCaption()); };
function tourTogglePlay() { tourPlaying = !tourPlaying; renderPlayBtn(); if (!tourPlaying) window.speechSynthesis?.cancel(); }
function renderPlayBtn() { $('#tPlay').innerHTML = tourPlaying ? PAUSE_SVG : PLAY_SVG; $('#tPlay').setAttribute('aria-label', tourPlaying ? 'Pause tour' : 'Play tour'); }
function startTour() {
  touring = true; tourPlaying = true; tourT = 0; tourCh = -1; renderPlayBtn();
  tourSaved = { view, speed: src.speed };
  document.body.classList.add('touring'); $('#tourBtn').lastChild.textContent = 'End tour';
  if (view !== 'smart') setView('smart');
  setSpeed(20); pinned = null;
  if (paused) togglePause();
}
function endTour() {
  if (!touring) return;
  touring = false; tourPlaying = false; window.speechSynthesis?.cancel(); syncMusic();
  document.body.classList.remove('touring'); $('#tourBtn').lastChild.textContent = 'Watch the tour';
  document.querySelectorAll('.spot').forEach((e) => e.classList.remove('spot'));
  if (tourSaved) setSpeed(tourSaved.speed);
  lastSwap = -99; if (summary) renderWhy(summary.notable);
}
function stopTourOnInput() { if (touring && tourPlaying && !CAPTURE) { tourPlaying = false; renderPlayBtn(); window.speechSynthesis?.cancel(); } }
function seekChapter(i) { let t = 0; for (let k = 0; k < i; k++) t += TOUR[k].dur; tourT = t + 0.001; tourCh = -1; tourPlaying = true; renderPlayBtn(); }
let tourRec = null;
function currentCaption() { const c = TOUR[tourCh]; return `${c.t}. ${$('#capB').textContent}`; }
function speak(text) {
  if (!voice || !('speechSynthesis' in window) || CAPTURE) return;
  try {
    speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.rate = 1.06;
    const vs = speechSynthesis.getVoices().filter((v) => /^en(-|_)/i.test(v.lang));   // prefer natural / neural voices
    u.voice = vs.find((v) => /natural|neural/i.test(v.name)) || vs.find((v) => /google/i.test(v.name)) || vs[0] || null;
    speechSynthesis.speak(u);
  } catch (e) { /* speech unavailable */ }
}
function enterChapter(i) {
  const c = TOUR[i]; tourCh = i;
  document.querySelectorAll('.spot').forEach((e) => e.classList.remove('spot'));
  if (c.spot) { const el = $(c.spot); if (el) { el.classList.add('spot'); if (el.closest('.console')) el.scrollIntoView({ block: 'nearest', behavior: CAPTURE ? 'auto' : 'smooth' }); } }
  let body = c.b;
  if (c.do !== 'decision' && c.do !== 'nearest') show3D(null);
  if (c.do === 'decision' || c.do === 'nearest') {
    if (c.do === 'decision' || !tourRec) tourRec = summary && summary.notable;
    selectTab('why');
    if (tourRec) {
      renderWhy(tourRec);
      const r = tourRec, mx = (r.x + r.chosen.rx) / 2, mz = (r.z + r.chosen.rz) / 2;
      cam.flyTo({ t: [mx, 0, mz], yaw: c.do === 'decision' ? cam.yaw + 0.5 : cam.yaw - 0.35, pitch: 0.82, dist: c.do === 'decision' ? 95 : 80 }, 2.2);
      if (c.do === 'nearest') body = r.base && !r.base.same ? `The nearest-car rule would have sent ${car(r.base.id)}, shown in grey. ${r.base.reason}` : 'Here both dispatchers agree, which happens often. ORCA only differs when the numbers say it should.';
    }
  } else if (c.do === 'outage') {
    src.command({ cmd: 'outage' });
    pendingFly = 'outage';
  } else if (c.do === 'spike') {
    src.command({ cmd: 'spike' }); selectTab('fc'); pendingFly = 'spike';
  } else if (c.do === 'result') {
    body = resultText();
  }
  if (c.cam) cam.flyTo(c.cam, i === 0 ? 3 : 2.4);
  const cap = $('#caption'); cap.classList.add('swap');
  const apply = () => { $('#capCh').textContent = `Chapter ${i + 1} of ${TOUR.length}`; $('#capT').textContent = c.t; $('#capB').textContent = body; cap.classList.remove('swap'); speak(`${c.t}. ${body}`); };
  if (CAPTURE) apply(); else setTimeout(apply, 220);
}
function resultText() {   // same window as the scoreboard (last 500 pickups), so the two always agree
  const a = summary.stats.smart, b = summary.stats.base, g = b.mean ? Math.round(((b.mean - a.mean) / b.mean) * 100) : 0;
  return `Over the last 500 pickups on these same requests, ORCA riders waited ${mmss(a.mean)} on average against ${mmss(b.mean)} with the nearest car: ${g}% less. Urgent riders and the slowest pickups improve the most.`;
}
let pendingFly = null;
function tourTick(dt) {
  if (!touring) return;
  if (tourPlaying) tourT += dt;
  if (tourT >= TOUR_TOTAL) { endTour(); if (CAPTURE) window.__orcaDone = true; return; }
  let acc = 0, i = 0; while (i < TOUR.length - 1 && tourT >= acc + TOUR[i].dur) { acc += TOUR[i].dur; i++; }
  if (i !== tourCh) enterChapter(i);
  const c = TOUR[i];
  if (c.orbit && tourPlaying && !cam.tween) { cam.g.yaw += c.orbit * dt; }
  if (pendingFly && summary) {
    const arr = pendingFly === 'outage' ? summary.fx.outages : summary.fx.spikes, o = arr.at(-1);
    if (o) { cam.flyTo({ t: [o[0], 0, o[1]], yaw: cam.yaw + 0.4, pitch: 0.95, dist: pendingFly === 'outage' ? 230 : 210 }, 2.2); pendingFly = null; }
  }
  document.querySelectorAll('#tTrack .sg').forEach((s, k) => {
    let st = 0; for (let j = 0; j < k; j++) st += TOUR[j].dur;
    const p = clamp01((tourT - st) / TOUR[k].dur);
    s.querySelector('b').style.width = p * 100 + '%';
  });
  $('#tTime').textContent = `${mmss(tourT)} / ${mmss(TOUR_TOTAL)}`;
  if (c.do === 'result' && summary) { const t = resultText(); if ($('#capB').textContent !== t) $('#capB').textContent = t; }
  syncMusic();
}

// ----------------------------------------------------------------------------- main loop (and a deterministic capture mode for recording video)
function step(dt, draw = true) {
  clock += dt;
  src.tick(dt);
  tourTick(dt);
  keyboardCam(dt);
  cam.update(dt);
  drawFrame(dt); drawFx(); drawExplain();
  if (!touring) { /* gentle idle drift is off: the user owns the camera */ }
  tweenBoard(dt); updateCursor();
  R.time = clock;
  if (draw) R.render(cam);
}
addEventListener('resize', resize);
resize();
(async () => {
  src = await pickSource();
  src.on('frame', onFrame); src.on('summary', onSummary); src.on('status', setStatus);
  setStatus(src.kind === 'live' ? 'live' : 'demo');
  if (src.kind === 'live') document.querySelectorAll('[data-speed]').forEach((x) => x.setAttribute('aria-pressed', +x.dataset.speed === src.speed));
  src.start();
  if (CAPTURE) {
    $('#welcome').hidden = true; document.body.classList.add('capture');
    window.__orca = { step: (dt, draw = true) => step(dt, draw), skip: (sec, dt = 1 / 30) => { for (let k = 0; k < Math.round(sec / dt); k++) step(dt, false); }, startTour, total: TOUR_TOTAL, chapters: TOUR.map((c) => ({ t: c.t, dur: c.dur })) };
    cam.flyTo(VIEWS.home, 0.001); step(0.016);
    document.title = 'capture-ready';
    return;
  }
  cam.flyTo(VIEWS.home, reduceMotion ? 0.001 : 3.4);   // the one orchestrated entrance: plan view settles into the model
  let last = performance.now();
  const loop = (now) => { requestAnimationFrame(loop); const dt = Math.min(0.1, (now - last) / 1000); last = now; step(dt); };
  requestAnimationFrame(loop);
})();
