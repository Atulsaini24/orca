// ============================================================================
// ORCA simulation core: city, demand, traffic, fleet and two dispatch policies.
// No rendering dependencies, so it runs in the browser and headless in Node.
// ============================================================================
const CFG = {
  GRID: 28,            // intersections per axis
  CELL: 10,            // world units between roads (1 unit ~ 30 m)
  ZONES: 7,            // forecast zones per axis (7 x 7 = 49)
  BASE_SPEED: 0.3,     // units / sim-second (~32 km/h)
  ART_MULT: 1.55,      // arterial roads are faster
  OFFER_TIMEOUT: 20,   // seconds before an unanswered offer expires
  STALE_AFTER: 15,     // GPS older than this is treated as unreliable
  ABANDON: 900,        // rider cancels after 15 min without pickup
  BATCH: 3,            // smart policy matches in 3 s batches
  REBAL: 20,           // rebalancing cadence
  K: 10,               // candidates per request
  REOFFER: 45,         // expected cost (s) of a declined / expired offer
  COVER: 0.5,          // idle cars wanted per zone, as a share of its forecast 5-min demand
  ZONE_SLOPE: 20,      // seconds of cost per unit of zone shortfall
  ZONE_CAP: 60,        // maximum zone supply cost (s)
  URGENT_W: 0.55,      // urgent requests' cost multiplier
  WAIT_W: 0.25,        // cost credit per second already waited
  DEADLINE_W: 1.0,     // give-up penalty weight (riders cancel after ABANDON seconds)
  START_CLOCK: 7 * 3600 + 30 * 60,
};
CFG.W = (CFG.GRID - 1) * CFG.CELL;
if (typeof CITY_TUNED !== 'undefined') Object.assign(CFG, CITY_TUNED);   // weights tuned for this city (orca.tune)
CFG.ZSIZE = CFG.W / CFG.ZONES;

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash2(a, b) {
  let h = Math.imul(a ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul((b + 0x632be59b) | 0, 0xc2b2ae35);
  h ^= h >>> 16; h = Math.imul(h, 0x7feb352d); h ^= h >>> 15; h = Math.imul(h, 0x846ca68b); h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const isArterial = (i) => i % 7 === 0 || i === CFG.GRID - 1;
const snap = (v) => clamp(Math.round(v / CFG.CELL), 0, CFG.GRID - 1) * CFG.CELL;
function zoneOfGrid(x, z) {
  const zx = clamp(Math.floor(x / CFG.ZSIZE), 0, CFG.ZONES - 1);
  const zz = clamp(Math.floor(z / CFG.ZSIZE), 0, CFG.ZONES - 1);
  return zz * CFG.ZONES + zx;
}
const zoneOf = (x, z) => city().zoneOf(x, z);
const zoneName = (i) => city().zoneName(i);
const zoneDist = (a, b) => city().zoneDist(a, b);
function gauss(rng) {
  let u = 0; while (u === 0) u = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
}
function poisson(rng, lam) {
  if (lam > 30) return Math.max(0, Math.round(lam + Math.sqrt(lam) * gauss(rng)));
  const L = Math.exp(-lam); let k = 0, p = 1;
  do { k++; p *= rng(); } while (p > L);
  return k - 1;
}

// ---------------------------------------------------------------------------
// Real demand: a sample of the city's real trips (data/<city>/trips.js, from scripts/prepare_trips.py)
// replayed at the real hour-by-hour rate, plus real Manhattan speed by hour.
// ---------------------------------------------------------------------------
function decodeTLC(d) {
  if (!d || !d.b64) return null;
  const bin = atob(d.b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const n = d.n, sec = new Uint32Array(bytes.buffer, 0, n), q = new Uint16Array(bytes.buffer, 4 * n, 4 * n), res = d.res || 100;
  const tph = d.trips_per_hour, peak = Math.max(...tph) / 3600;
  return { n, sec, q, res, tph, peak, city: d.city || 'grid', traffic: d.traffic.map((f) => clamp(f, 0.7, 1.6)), source: d.source,
    total: d.total, days: d.days, peakTrip: d.peak_trip_s || 630 };
}
const REAL_ALL = decodeTLC(typeof TLC_DATA !== 'undefined' ? TLC_DATA : null);
let REAL = null;          // set by World: the real trips, if they were projected for the active city
const hourly = (arr, sec) => { const h = (sec / 3600) % 24, i = Math.floor(h), f = h - i; return arr[i] * (1 - f) + arr[(i + 1) % 24] * f; };
function lowerBound(a, v) { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m; } return lo; }

// ---------------------------------------------------------------------------
// World: shared by both policies so they face the identical request stream.
// ---------------------------------------------------------------------------
class World {
  constructor(seed, fleet) {
    this.seed = seed; this.rng = mulberry32(seed); this.fleet = fleet;
    this.t = 0; this.demandMult = 1; this.nextReqId = 1; this.outageSeq = 0;
    this.hotspots = []; this.blobs = []; this.outages = []; this.events = []; this.listeners = [];
    const c = this.city = city(), r = this.rng;
    REAL = REAL_ALL && REAL_ALL.city === c.name ? REAL_ALL : null;
    this.heatCell = Math.max(c.WX, c.WZ) / 54;
    this.HEAT_W = Math.ceil(c.WX / this.heatCell); this.HEAT_H = Math.ceil(c.WZ / this.heatCell); this.HEAT = this.HEAT_W;
    this.heat = new Float32Array(this.HEAT_W * this.HEAT_H);
    for (let i = 0; i < 5; i++) {
      const [x, z] = this.innerPoint(0.15);
      this.hotspots.push({ x, z, vx: (r() - 0.5) * 0.04, vz: (r() - 0.5) * 0.04, s: 14 + r() * 16, w: 0.6 + r(), until: Infinity });
    }
    const [cx, cz] = c.snap(c.WX * 0.5, c.WZ * 0.48);
    this.hotspots.push({ x: cx, z: cz, vx: 0, vz: 0, s: 30, w: 1.6, until: Infinity, core: true });
    if (c.ambientCongestion) for (let i = 0; i < 4; i++) {
      const [x, z] = c.randomPoint(r);
      this.blobs.push({ x, z, vx: (r() - 0.5) * 0.05, vz: (r() - 0.5) * 0.05, r: 22 + r() * 20, s: 0.35 + r() * 0.25, until: Infinity });
    }
    this.spikeBoost = 0;
  }
  clock() { return CFG.START_CLOCK + this.t; }
  innerPoint(m) { const c = this.city, r = this.rng; return c.snap(c.WX * (m + (1 - 2 * m) * r()), c.WZ * (m + (1 - 2 * m) * r())); }
  rushFactor() {
    const h = (this.clock() / 3600) % 24;
    return 1 + 0.35 * Math.exp(-((h - 8.75) ** 2) / (2 * 0.8 ** 2)) + 0.12 * Math.sin(this.t / 900);
  }
  rate() {
    if (REAL) {   // the real rate for this hour, scaled so the busiest hour is 1.25x what the fleet is built for
      const peakRate = this.city.name === 'grid' ? 1.25 * this.fleet * 0.00118 : 0.8 * this.fleet / (REAL.peakTrip + 300);
      const keep = peakRate / REAL.peak;
      return (keep * hourly(REAL.tph, this.clock()) / 3600) * this.demandMult * (1 + this.spikeBoost);
    }
    return this.fleet * 0.00118 * this.city.demandScale * this.demandMult * this.rushFactor() * (1 + this.spikeBoost);
  }
  trafficFactor() { return REAL ? hourly(REAL.traffic, this.clock()) : 1.05 - 0.15 * (this.rushFactor() - 1); }
  realTrip() {   // a real trip that started within 15 minutes (time of day) of now
    const s = this.clock() % 86400, r = this.rng, S = REAL.sec;
    let lo = lowerBound(S, s - 900), hi = lowerBound(S, s + 900);
    let i;
    if (s - 900 < 0) { const a = lowerBound(S, s - 900 + 86400); const span = (REAL.n - a) + hi; i = Math.floor(r() * span); i = i < REAL.n - a ? a + i : i - (REAL.n - a); }
    else if (s + 900 > 86400) { const b = lowerBound(S, s + 900 - 86400); const span = (REAL.n - lo) + b; i = Math.floor(r() * span); i = i < REAL.n - lo ? lo + i : i - (REAL.n - lo); }
    else i = hi > lo ? lo + Math.floor(r() * (hi - lo)) : Math.floor(r() * REAL.n);
    const q = REAL.q, k = 4 * i, f = REAL.res;
    return [q[k] / f, q[k + 1] / f, q[k + 2] / f, q[k + 3] / f];
  }
  congestion(x, z) {
    let m = 1;
    for (const b of this.blobs) {
      const dx = x - b.x, dz = z - b.z;
      m *= 1 - b.s * Math.exp(-(dx * dx + dz * dz) / (2 * b.r * b.r));
    }
    const local = REAL && this.city.zoneHour ? this.city.traffic(x, z, this.clock()) : this.trafficFactor();
    return Math.max(0.22, m * local);
  }
  step(dt) {
    this.t += dt;
    const WX = this.city.WX, WZ = this.city.WZ;
    for (const arr of [this.hotspots, this.blobs]) {
      for (const h of arr) {
        h.x += h.vx * dt; h.z += h.vz * dt;
        if (h.x < 10 || h.x > WX - 10) h.vx *= -1;
        if (h.z < 10 || h.z > WZ - 10) h.vz *= -1;
      }
    }
    const t = this.t;
    this.hotspots = this.hotspots.filter((h) => h.until > t);
    this.blobs = this.blobs.filter((b) => b.until > t);
    this.outages = this.outages.filter((o) => o.until > t);
    this.spikeBoost = this.hotspots.reduce((s, h) => s + (h.spike ? h.boost : 0), 0);
    const dec = Math.exp(-dt / 200);
    for (let i = 0; i < this.heat.length; i++) this.heat[i] *= dec;
    const n = poisson(this.rng, this.rate() * dt);
    const out = [];
    for (let i = 0; i < n; i++) out.push(this.makeRequest());
    return out;
  }
  sampleLoc() {
    const r = this.rng;
    if (r() < 0.68) {
      let tot = 0; for (const h of this.hotspots) tot += h.w;
      let pick = r() * tot, hs = this.hotspots[0];
      for (const h of this.hotspots) { pick -= h.w; if (pick <= 0) { hs = h; break; } }
      return [clamp(hs.x + gauss(r) * hs.s, 0, this.city.WX), clamp(hs.z + gauss(r) * hs.s, 0, this.city.WZ)];
    }
    return this.city.randomPoint(r);
  }
  makeRequest() {
    const r = this.rng;
    const spike = REAL && this.spikeBoost > 0 && r() < this.spikeBoost / (1 + this.spikeBoost);
    let x, z, dx, dz;
    if (REAL && !spike) { [x, z, dx, dz] = this.realTrip(); }
    else { [x, z] = spike ? this.sampleSpike() : this.sampleLoc(); }
    const c = this.city, [px, pz] = c.snap(x, z);
    if (dx === undefined) {
      const d = 20 - Math.log(1 - r()) * 45, a = r() * Math.PI * 2;
      dx = clamp(px + Math.cos(a) * d, 0, c.WX); dz = clamp(pz + Math.sin(a) * d, 0, c.WZ);
    }
    [dx, dz] = c.snap(dx, dz);
    if (dx === px && dz === pz) [dx, dz] = c.snap(px > c.WX / 2 ? px - 30 : px + 30, pz);
    const req = { id: this.nextReqId++, t: this.t, x: px, z: pz, dx, dz, p1: r() < 0.08, zone: zoneOf(px, pz) };
    const HW = this.HEAT_W, HH = this.HEAT_H, hx = Math.floor(px / this.heatCell), hz = Math.floor(pz / this.heatCell);
    for (let oz = -2; oz <= 2; oz++) for (let ox = -2; ox <= 2; ox++) {
      const X = hx + ox, Z = hz + oz;
      if (X < 0 || Z < 0 || X >= HW || Z >= HH) continue;
      this.heat[Z * HW + X] += Math.exp(-(ox * ox + oz * oz) / 2.2);
    }
    return req;
  }
  sampleSpike() {
    const hs = this.hotspots.filter((h) => h.spike), r = this.rng;
    const h = hs[Math.floor(r() * hs.length)];
    return [clamp(h.x + gauss(r) * h.s, 0, this.city.WX), clamp(h.z + gauss(r) * h.s, 0, this.city.WZ)];
  }
  log(type, msg) {
    const e = { t: this.t, clock: this.clock(), type, msg };
    this.events.push(e); if (this.events.length > 200) this.events.shift();
    for (const f of this.listeners) f(e);
  }
  spot(at) {
    if (!at) return this.innerPoint(0.2);
    const c = this.city;
    return c.snap(Math.min(c.WX, Math.max(0, at[0])), Math.min(c.WZ, Math.max(0, at[1])));
  }
  triggerSpike(at) {
    const [sx, sz] = this.spot(at);
    const h = { x: sx, z: sz, vx: 0, vz: 0, s: 16, w: 3.2,
      until: this.t + 600, spike: true, boost: 0.55 };
    this.hotspots.push(h);
    this.log('spike', `Demand spike near ${zoneName(zoneOf(h.x, h.z))}: +55% city demand for 10 min`);
    return h;
  }
  triggerOutage(at) {
    const [ox, oz] = this.spot(at);
    const o = { id: ++this.outageSeq, x: ox, z: oz, r: 55, until: this.t + 420 };
    this.outages.push(o);
    this.log('outage', `Network outage around ${zoneName(zoneOf(o.x, o.z))}: cars inside stop reporting GPS`);
    return o;
  }
  triggerAccident(at) {
    const [bx, bz] = this.spot(at);
    const b = { x: bx, z: bz, vx: 0, vz: 0, r: 20, s: 0.78,
      until: this.t + 900, accident: true };
    this.blobs.push(b);
    this.log('accident', `Major accident near ${zoneName(zoneOf(b.x, b.z))}: road speed down ~75%`);
    return b;
  }
}

function makeFleet(seed, n) {
  const r = mulberry32(seed * 7 + 1), out = [];
  for (let i = 0; i < n; i++) {
    const [x, z] = city().randomPoint(r);
    let acc = clamp(0.88 + gauss(r) * 0.07, 0.5, 0.99);
    if (r() < 0.12) acc = 0.3 + r() * 0.2; // picky drivers
    out.push({ id: i, x, z, hx: 0, hz: 1, node: -1, prevNode: -1, state: 'idle', acc, offlineUntil: -1, lastPing: 0,
      nextPing: r() * 4, rx: x, rz: z });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Dispatcher: one fleet + one policy ('smart' = ORCA, 'baseline' = nearest unit)
// ---------------------------------------------------------------------------
class Dispatcher {
  constructor(policy, world, init) {
    this.policy = policy; this.world = world; this.smart = policy === 'smart';
    this.rng = mulberry32(world.seed ^ 0xabc);
    this.agents = init.map((a) => ({ ...a, route: [], ri: 0, rs: null, rebal: false, offers: 0, accepts: 0,
      cruiseWait: 0, offerAt: 0, offerDeadline: 0, offerAcc: false }));
    this.queue = []; this.active = [];
    this.batchTimer = 0; this.rebalTimer = 0; this.sampleTimer = 0; this.zTimer = 0;
    this.batchNo = 0; this.lastOutage = 0; this.offline = 0; this.rebalMoved = 0; this.lastRebalLog = -1e9;
    this.m = { served: 0, lost: 0, reoffers: 0, timeouts: 0, declines: 0, waits: [], p1waits: [],
      intervalWaits: [], intervalLost: 0, series: [] };
    this.explain = new Map(); this.explainOrder = []; this.notable = null;
    this.city = city();
    const N = this.city.N;
    this.zArr = new Float32Array(N); this.zRate = new Float32Array(N); this.zPrev = new Float32Array(N);
    this.zIdle = new Int32Array(N); this.zForecast = new Float32Array(N); this.zLast = new Float32Array(N);
    this.zInit = false; this.deficitZones = [];
    this.zSum = new Float64Array(N); this.zN = new Int32Array(N); this.hist = new Int32Array(12);
    this.timelines = new Map(); this.tlOrder = [];
  }

  step(dt, newReqs) {
    const w = this.world, t = w.t;
    for (const o of w.outages) {
      if (o.id > this.lastOutage) {
        this.lastOutage = o.id;
        for (const a of this.agents) {
          const dx = a.x - o.x, dz = a.z - o.z;
          if (dx * dx + dz * dz < o.r * o.r) a.offlineUntil = Math.max(a.offlineUntil, o.until);
        }
      }
    }
    let offline = 0;
    for (const a of this.agents) {
      if (a.offlineUntil < t && this.rng() < 0.00003 * dt) a.offlineUntil = t + 120 + this.rng() * 300;
      const online = a.offlineUntil < t;
      if (!online) offline++;
      if (online && t >= a.nextPing) { a.rx = a.x; a.rz = a.z; a.lastPing = t; a.nextPing = t + 3 + this.rng() * 2; }
      if (a.state === 'offered') {
        const rs = a.rs;
        if (!rs || rs.status !== 'offered' || rs.agent !== a) { a.state = 'idle'; a.rs = null; }
        else if (online && t >= a.offerAt) this.offerResult(a, a.offerAcc);
        else if (t >= a.offerDeadline) this.offerTimeout(a);
        continue;
      }
      this.move(a, dt);
      if (a.ri >= a.route.length) {
        if (a.state === 'idle') {
          a.rebal = false; a.cruiseWait -= dt;
          if (a.cruiseWait <= 0) { this.cruise(a); a.cruiseWait = 10 + this.rng() * 30; }
        } else this.arrive(a);
      }
    }
    this.offline = offline;

    for (const r of newReqs) this.onRequest(r);

    for (const rs of this.active) {
      if ((rs.status === 'waiting' || rs.status === 'offered' || rs.status === 'assigned') && t - rs.r.t > CFG.ABANDON) this.lose(rs);
    }
    this.active = this.active.filter((rs) => rs.status === 'waiting' || rs.status === 'offered' || rs.status === 'assigned');

    if (this.smart) {
      this.zTimer += dt;
      if (this.zTimer >= 60) { this.zTimer -= 60; this.updateForecast(); }
      this.batchTimer += dt;
      if (this.batchTimer >= CFG.BATCH) { this.batchTimer -= CFG.BATCH; this.dispatchSmart(); }
      this.rebalTimer += dt;
      if (this.rebalTimer >= CFG.REBAL) { this.rebalTimer -= CFG.REBAL; this.rebalance(); }
    } else this.dispatchBaseline();

    this.sampleTimer += dt;
    if (this.sampleTimer >= 60) {
      this.sampleTimer -= 60;
      const iw = this.m.intervalWaits;
      const mean = iw.length ? iw.reduce((a, b) => a + b, 0) / iw.length : null;
      this.m.series.push({ t, wait: mean, lost: this.m.intervalLost });
      if (this.m.series.length > 120) this.m.series.shift();
      this.m.intervalWaits = []; this.m.intervalLost = 0;
    }
  }

  // ---- movement ------------------------------------------------------------
  move(a, dt) {
    if (a.ri >= a.route.length) return;
    let rem = this.city.baseSpeed * this.world.congestion(a.x, a.z) * dt * (a.state === 'idle' && !a.rebal ? 0.55 : 1);
    let guard = 0;
    const R = a.route;
    while (rem > 0 && a.ri < R.length && guard++ < 64) {
      const wx = R[a.ri], wz = R[a.ri + 1], mult = R[a.ri + 2], node = R[a.ri + 3];
      const dx = wx - a.x, dz = wz - a.z, d = Math.hypot(dx, dz), step = rem * mult;
      if (d > 1e-9) { a.hx = dx / d; a.hz = dz / d; }
      if (d <= step) {
        a.x = wx; a.z = wz; rem -= d / mult; a.ri += 4;
        if (node >= 0) { a.prevNode = a.node; a.node = node; }
      } else { a.x += (dx / d) * step; a.z += (dz / d) * step; rem = 0; }
    }
  }
  routeTo(a, tx, tz) { a.route = this.city.route(a, tx, tz); a.ri = 0; }
  cruise(a) { a.route = this.city.cruise(a, this.rng); a.ri = 0; }
  arrive(a) {
    const t = this.world.t, rs = a.rs;
    if (a.state === 'pickup') {
      rs.status = 'ontrip'; rs.pickedAt = t;
      const wait = t - rs.r.t, m = this.m;
      m.served++; m.waits.push(wait); if (m.waits.length > 500) m.waits.shift();
      if (rs.r.p1) { m.p1waits.push(wait); if (m.p1waits.length > 150) m.p1waits.shift(); }
      m.intervalWaits.push(wait);
      this.zSum[rs.r.zone] += wait; this.zN[rs.r.zone]++; this.hist[Math.min(11, Math.floor(wait / 30))]++;
      rs.log.push({ t, k: 'pickup', car: a.id });
      a.state = 'trip'; this.routeTo(a, rs.r.dx, rs.r.dz);
    } else if (a.state === 'trip') {
      rs.status = 'done'; a.rs = null; a.state = 'idle'; a.route = []; a.ri = 0;
      a.cruiseWait = 5 + this.rng() * 20;
    }
  }

  // ---- requests & offers ----------------------------------------------------
  onRequest(r) {
    const rs = { r, status: 'waiting', agent: null, declined: new Set(), offers: 0, assignedAt: -1, pickedAt: -1, log: [{ t: this.world.t, k: 'request' }] };
    if (this.smart) { this.timelines.set(r.id, rs.log); this.tlOrder.push(r.id); if (this.tlOrder.length > 1500) this.timelines.delete(this.tlOrder.shift()); }
    this.queue.push(rs); this.active.push(rs);
    if (this.smart) this.zArr[r.zone]++;
  }
  lose(rs) {
    const a = rs.agent;
    if (a && a.rs === rs) { a.state = 'idle'; a.rs = null; a.route = []; a.ri = 0; a.cruiseWait = 2; }
    rs.status = 'lost'; rs.agent = null; this.m.lost++; this.m.intervalLost++; rs.log.push({ t: this.world.t, k: 'cancel' });
  }
  etaHat(ax, az, bx, bz) {
    const w = this.world, d = this.city.roadDist(ax, az, bx, bz);
    const c = (w.congestion(ax, az) + w.congestion(bx, az) + w.congestion(bx, bz)) / 3;
    return d / (this.city.baseSpeed * c * 1.12) + 4;
  }
  pTrue(a, eta, rs) {
    return clamp(a.acc - 0.03 * Math.max(0, eta / 60 - 4) + (rs.r.p1 ? 0.06 : 0), 0.03, 0.995);
  }
  // Online Bayesian estimate of each driver's acceptance (Beta prior, mean 0.82).
  pHat(a, eta, rs) {
    const accHat = (a.accepts + 8 * 0.82) / (a.offers + 8);
    return clamp(accHat - 0.03 * Math.max(0, eta / 60 - 4) + (rs.r.p1 ? 0.06 : 0), 0.03, 0.995);
  }
  offer(a, rs) {
    const t = this.world.t;
    a.state = 'offered'; a.rs = rs; a.route = []; a.ri = 0; a.rebal = false;
    rs.status = 'offered'; rs.agent = a; rs.offers++; a.offers++; rs.log.push({ t, k: 'offer', car: a.id });
    const p = this.pTrue(a, this.etaHat(a.x, a.z, rs.r.x, rs.r.z), rs);
    a.offerAcc = hash2(a.id * 131 + 7, rs.r.id * 7 + rs.offers) < p;
    a.offerAt = t + 4 + hash2(a.id, rs.r.id) * 7;
    a.offerDeadline = t + CFG.OFFER_TIMEOUT;
  }
  offerResult(a, accepted) {
    const rs = a.rs, t = this.world.t;
    if (accepted) {
      a.accepts++; a.state = 'pickup'; rs.status = 'assigned'; rs.assignedAt = t; rs.log.push({ t, k: 'accept', car: a.id });
      this.routeTo(a, rs.r.x, rs.r.z);
    } else { this.m.declines++; rs.log.push({ t, k: 'decline', car: a.id }); this.release(a, rs); }
  }
  offerTimeout(a) { this.m.timeouts++; a.rs.log.push({ t: this.world.t, k: 'expire', car: a.id }); this.release(a, a.rs); }
  release(a, rs) {
    this.m.reoffers++;
    rs.declined.add(a.id); rs.status = 'waiting'; rs.agent = null; this.queue.push(rs);
    a.state = 'idle'; a.rs = null; a.cruiseWait = 3;
  }

  // ---- spatial index ----------------------------------------------------------
  buildIndex(freshOnly) {
    const t = this.world.t, B = 30, BN = Math.ceil(Math.max(this.city.WX, this.city.WZ) / B) + 1;
    this.B = B; this.BN = BN;
    if (!this.buckets) this.buckets = Array.from({ length: BN * BN }, () => []);
    for (const b of this.buckets) b.length = 0;
    for (const a of this.agents) {
      if (a.state !== 'idle') continue;
      if (freshOnly && t - a.lastPing > CFG.STALE_AFTER) continue;
      const bx = clamp(Math.floor(a.rx / B), 0, BN - 1), bz = clamp(Math.floor(a.rz / B), 0, BN - 1);
      this.buckets[bz * BN + bx].push(a);
    }
  }
  kNearest(x, z, K, ok) {
    const B = this.B, BN = this.BN;
    const cx = clamp(Math.floor(x / B), 0, BN - 1), cz = clamp(Math.floor(z / B), 0, BN - 1);
    const found = []; let stopAt = -1;
    for (let r = 0; r < BN; r++) {
      for (let bz = cz - r; bz <= cz + r; bz++) {
        if (bz < 0 || bz >= BN) continue;
        for (let bx = cx - r; bx <= cx + r; bx++) {
          if (bx < 0 || bx >= BN || Math.max(Math.abs(bx - cx), Math.abs(bz - cz)) !== r) continue;
          for (const a of this.buckets[bz * BN + bx]) {
            if (a.state !== 'idle' || !ok(a)) continue;
            const dx = a.rx - x, dz = a.rz - z; found.push([dx * dx + dz * dz, a]);
          }
        }
      }
      if (found.length >= K) { if (stopAt < 0) stopAt = r + 1; if (r >= stopAt) break; }
    }
    found.sort((p, q) => p[0] - q[0]);
    return found.slice(0, K).map((f) => f[1]);
  }

  // ---- baseline: offer the nearest unit by straight-line distance, FIFO -------
  dispatchBaseline() {
    const q = this.queue.filter((rs) => rs.status === 'waiting');
    this.queue = [];
    if (!q.length) return;
    this.buildIndex(false);
    for (const rs of q) {
      const a = this.kNearest(rs.r.x, rs.r.z, 1, (u) => !rs.declined.has(u.id))[0];
      if (a) this.offer(a, rs); else this.queue.push(rs);
    }
  }

  // ---- ORCA: batched, cost-based matching ------------------------------------
  zoneCost(a) {
    const z = zoneOf(a.rx, a.rz), ratio = this.zForecast[z] * CFG.COVER / (this.zIdle[z] + 1);
    return ratio > 1 ? Math.min(CFG.ZONE_CAP, (ratio - 1) * CFG.ZONE_SLOPE) : 0;
  }
  computeZoneIdle() {
    const t = this.world.t; this.zIdle.fill(0);
    for (const a of this.agents) if (a.state === 'idle' && t - a.lastPing <= CFG.STALE_AFTER) this.zIdle[zoneOf(a.rx, a.rz)]++;
  }
  updateForecast() {
    const N = this.zArr.length;
    for (let i = 0; i < N; i++) {
      const rate = this.zArr[i] / 60;
      this.zLast[i] = this.zArr[i]; this.zArr[i] = 0;
      this.zPrev[i] = this.zRate[i];
      this.zRate[i] = this.zInit ? 0.65 * this.zRate[i] + 0.35 * rate : rate;
      const trend = this.zInit ? clamp(this.zRate[i] / (this.zPrev[i] + 1e-3), 0.7, 1.5) : 1;
      this.zForecast[i] = this.zRate[i] * 300 * trend;
    }
    this.zInit = true;
  }
  dispatchSmart() {
    const t = this.world.t; this.batchNo++;
    const q = this.queue.filter((rs) => rs.status === 'waiting');
    this.queue = [];
    if (!q.length) return;
    this.buildIndex(true); this.computeZoneIdle();
    const pairs = [];
    for (const rs of q) {
      const cands = this.kNearest(rs.r.x, rs.r.z, CFG.K, (a) => !rs.declined.has(a.id));
      rs._cands = [];
      const wait = t - rs.r.t;
      for (const a of cands) {
        const eta = this.etaHat(a.rx, a.rz, rs.r.x, rs.r.z);
        const p = this.pHat(a, eta, rs), zc = this.zoneCost(a);
        const cost = eta + (1 - p) * CFG.REOFFER + zc;
        const slack = CFG.ABANDON - (wait + eta + 30);                    // the rider gives up at ABANDON: see dispatch.py
        const adj = cost * (rs.r.p1 ? CFG.URGENT_W : 1) - Math.min(wait, 300) * CFG.WAIT_W
          + (CFG.DEADLINE_W > 0 ? (CFG.DEADLINE_W * 600) / (1 + Math.exp(slack / 40)) : 0);
        const c = { a, eta, p, zc, cost, age: t - a.lastPing, rx: a.rx, rz: a.rz };
        rs._cands.push(c); pairs.push([adj, rs, c]);
      }
    }
    pairs.sort((x, y) => x[0] - y[0]);
    for (const [, rs, c] of pairs) {
      if (rs.status !== 'waiting' || c.a.state !== 'idle') continue;
      this.offer(c.a, rs); this.recordExplain(rs, c);
    }
    for (const rs of q) { rs._cands = null; if (rs.status === 'waiting') this.queue.push(rs); }
  }
  recordExplain(rs, chosen) {
    const t = this.world.t, r = rs.r;
    let list = rs._cands.slice().sort((a, b) => a.cost - b.cost).slice(0, 5);
    if (!list.includes(chosen)) list[list.length - 1] = chosen;
    // What would the nearest-unit policy have picked? (straight line, ignores GPS age)
    let best = null, bd = Infinity;
    for (const a of this.agents) {
      if (!(a.state === 'idle' || a === chosen.a) || rs.declined.has(a.id)) continue;
      const dx = a.rx - r.x, dz = a.rz - r.z, d = dx * dx + dz * dz;
      if (d < bd) { bd = d; best = a; }
    }
    let base = null;
    if (best) {
      const eta = this.etaHat(best.rx, best.rz, r.x, r.z), p = this.pHat(best, eta, rs);
      const zc = this.zoneCost(best), age = t - best.lastPing;
      const cost = eta + (1 - p) * CFG.REOFFER + zc;
      let reason;
      if (best === chosen.a) reason = 'Same unit. Both policies agree here.';
      else if (age > CFG.STALE_AFTER) reason = `GPS last seen ${Math.round(age)}s ago. The car is probably unreachable, so the offer would hang until it times out.`;
      else if (p < chosen.p - 0.12) reason = `Learned acceptance is only ${Math.round(p * 100)}%. A decline costs about ${Math.round(CFG.REOFFER)}s of re-offer time.`;
      else if (eta > chosen.eta + 8) reason = `Closer in a straight line, but ${Math.round(eta - chosen.eta)}s slower by road and traffic.`;
      else if (zc > 5) reason = `It is one of the last idle cars in ${zoneName(zoneOf(best.rx, best.rz))}, where demand is about to rise.`;
      else reason = 'Nearest car, but a higher total expected cost.';
      base = { id: best.id, eta, p, zc, cost, age, rx: best.rx, rz: best.rz, reason, same: best === chosen.a };
    }
    const rec = {
      id: r.id, clock: this.world.clock(), p1: r.p1, zone: r.zone, batch: this.batchNo, x: r.x, z: r.z,
      wait: t - r.t, attempt: rs.offers,
      chosen: { id: chosen.a.id, eta: chosen.eta, p: chosen.p, zc: chosen.zc, cost: chosen.cost, age: chosen.age, rx: chosen.rx, rz: chosen.rz },
      cands: list.map((c) => ({ id: c.a.id, eta: c.eta, p: c.p, zc: c.zc, cost: c.cost, age: c.age, rx: c.rx, rz: c.rz, chosen: c === chosen,
        taken: c !== chosen && c.a.state !== 'idle' })),
      base,
    };
    this.explain.set(r.id, rec); this.explainOrder.push(r.id);
    if (this.explainOrder.length > 800) this.explain.delete(this.explainOrder.shift());
    if (base && !base.same && (r.p1 || base.age > CFG.STALE_AFTER || this.rng() < 0.25)) this.notable = rec;
  }
  rebalance() {
    const t = this.world.t, N = this.city.N;
    this.computeZoneIdle();
    const deficit = [], surplusLeft = new Map();
    for (let i = 0; i < N; i++) {
      const d = this.zForecast[i] * CFG.COVER - this.zIdle[i];
      if (d > 1.5) deficit.push([i, d]); else if (-d > 2) surplusLeft.set(i, Math.floor(-d));
    }
    deficit.sort((a, b) => b[1] - a[1]);
    this.deficitZones = deficit.slice(0, 8).map((d) => d[0]);
    const byZone = Array.from({ length: N }, () => []);
    for (const a of this.agents) {
      if (a.state === 'idle' && !a.rebal && t - a.lastPing <= CFG.STALE_AFTER) byZone[zoneOf(a.rx, a.rz)].push(a);
    }
    let budget = Math.ceil(this.agents.length * 0.015), moved = 0;
    for (const [zi, d] of deficit) {
      let want = Math.min(Math.ceil(d), budget);
      const cand = [...surplusLeft.keys()].filter((k) => surplusLeft.get(k) > 0 && zoneDist(k, zi) < 2.9)
        .sort((p, q) => zoneDist(p, zi) - zoneDist(q, zi));
      for (const sz of cand) {
        const pool = byZone[sz];
        while (want > 0 && surplusLeft.get(sz) > 0 && pool.length) {
          const a = pool.pop();
          const [tx, tz] = this.city.pointInZone(zi, this.rng);
          this.routeTo(a, tx, tz); a.rebal = true; a.cruiseWait = 0;
          want--; budget--; moved++; surplusLeft.set(sz, surplusLeft.get(sz) - 1);
        }
        if (want <= 0) break;
      }
      if (budget <= 0) break;
    }
    this.rebalMoved = moved;
    if (moved >= 12 && t - this.lastRebalLog > 420 && deficit.length) {
      this.lastRebalLog = t;
      this.world.log('rebal', `ORCA moved ${moved} idle cars toward ${deficit.slice(0, 3).map((d) => zoneName(d[0])).join(', ')}`);
    }
  }

  stats() {
    const s = this.m.waits.slice().sort((a, b) => a - b), t = this.world.t;
    const mean = s.length ? s.reduce((a, b) => a + b, 0) / s.length : 0;
    const pct = (q) => (s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0);
    const p1 = this.m.p1waits, p1m = p1.length ? p1.reduce((a, b) => a + b, 0) / p1.length : 0;
    let busy = 0, idle = 0, off = 0, waiting = 0;
    for (const a of this.agents) { if (a.state === 'idle') idle++; else busy++; if (a.offlineUntil >= t) off++; }
    for (const rs of this.active) if (rs.status === 'waiting' || rs.status === 'offered') waiting++;
    return { mean, p50: pct(0.5), p95: pct(0.95), p1: p1m, served: this.m.served, lost: this.m.lost,
      reoffers: this.m.reoffers, timeouts: this.m.timeouts, util: busy / this.agents.length, idle, offline: off, waiting };
  }
}

class Simulation {
  constructor({ seed = 7, fleet = 1200 } = {}) {
    this.world = new World(seed, fleet);
    const init = makeFleet(seed, fleet);
    this.smart = new Dispatcher('smart', this.world, init);
    this.base = new Dispatcher('baseline', this.world, init);
  }
  step(dt) {
    const reqs = this.world.step(dt);
    this.smart.step(dt, reqs); this.base.step(dt, reqs);
  }
}
