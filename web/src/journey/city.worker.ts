// Decodes a city's real data files (frontend/data/<city>/{streets,buildings}.js, the same files the control room
// uses) into ready-to-upload geometry, off the main thread. Everything is centred on the city's middle.
import type { CityGeo } from './types';

const b64 = (s: string) => { const bin = atob(s), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; };
const u16 = (s: string) => { const b = b64(s); return new Uint16Array(b.buffer, b.byteOffset, b.byteLength >> 1); };
const u32 = (s: string) => { const b = b64(s); return new Uint32Array(b.buffer, b.byteOffset, b.byteLength >> 2); };
const q = (s: string) => Float32Array.from(u16(s), (v) => v / 20);

async function load(url: string) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  const t = await r.text();
  return JSON.parse(t.slice(t.indexOf('{'), t.lastIndexOf('}') + 1));
}

type Streets = {
  label: string; short: string; WX: number; WZ: number; unit_m: number; n_nodes: number; n_edges: number;
  node_x: string; node_z: string; src: string; dst: string; cls: string; geo_off: string; geo_x: string; geo_z: string;
  class_speed: number[]; class_major: number[]; zones: { id: number; name: string }[]; zone_ring_off: number[]; ring_off: number[];
  ring_x: string; ring_z: string; parks?: number[][];
};
type Buildings = { n: number; unit_m: number; counts: string; x: string; z: string; h: string; year: string; source: string };

async function build(city: string, base: string): Promise<{ geo: CityGeo; transfer: Transferable[] }> {
  const [S, B] = await Promise.all([load(`${base}/${city}/streets.js`) as Promise<Streets>, load(`${base}/${city}/buildings.js`) as Promise<Buildings>]);
  const ox = S.WX / 2, oz = S.WZ / 2;

  // ---- street graph
  const nx = q(S.node_x).map((v) => v - ox), nz = q(S.node_z).map((v) => v - oz);
  const src = u16(S.src), dst = u16(S.dst), cls = b64(S.cls), geoOff = u32(S.geo_off);
  const gx = q(S.geo_x).map((v) => v - ox), gz = q(S.geo_z).map((v) => v - oz);
  const E = src.length;
  // street lines: every polyline segment once (both directions of a two-way street share geometry)
  const seen = new Set<number>(), seg: number[] = [], major: number[] = [];
  for (let e = 0; e < E; e++) {
    const a = src[e], b = dst[e], key = a < b ? a * 65536 + b : b * 65536 + a;
    if (seen.has(key)) continue; seen.add(key);
    const m = S.class_major[cls[e]] ? 1 : 0;
    for (let k = geoOff[e] + 1; k < geoOff[e + 1]; k++) { seg.push(gx[k - 1], 0.05, gz[k - 1], gx[k], 0.05, gz[k]); major.push(m, m); }
  }

  // ---- buildings: walls + fan-triangulated roof, with per-vertex info for the shader
  const counts = b64(B.counts), X = u16(B.x), Z = u16(B.z), H = u16(B.h);
  let nv = 0, ni = 0;
  for (let i = 0; i < B.n; i++) { const k = counts[i]; nv += 4 * k + k; ni += 6 * k + 3 * Math.max(0, k - 2); }
  const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3), uv = new Float32Array(nv * 2), info = new Float32Array(nv * 4);
  const idx = new Uint32Array(ni);
  let v = 0, t = 0, o = 0, tallest = 0;
  const pts: number[] = [];
  for (let b = 0; b < B.n; b++) {
    const k = counts[b], h = Math.max(0.12, H[b] / 1000);
    tallest = Math.max(tallest, h);
    pts.length = 0; let cx = 0, cz = 0;
    for (let j = 0; j < k; j++) { const x = X[o + j] / 20 - ox, z = Z[o + j] / 20 - oz; pts.push(x, z); cx += x / k; cz += z / k; }
    o += k;
    const rnd = (Math.sin(b * 12.9898) * 43758.5453) % 1;
    const put = (x: number, y: number, z: number, nx_: number, ny: number, nz_: number, u: number, w: number) => {
      pos[v * 3] = x; pos[v * 3 + 1] = y; pos[v * 3 + 2] = z; nor[v * 3] = nx_; nor[v * 3 + 1] = ny; nor[v * 3 + 2] = nz_;
      uv[v * 2] = u; uv[v * 2 + 1] = w; info[v * 4] = cx; info[v * 4 + 1] = cz; info[v * 4 + 2] = h; info[v * 4 + 3] = Math.abs(rnd);
      return v++;
    };
    // walls (drawn double-sided; the normal is turned to face away from the footprint's centre)
    let along = 0;
    for (let j = 0; j < k; j++) {
      const jb = (j + 1) % k;
      const xa = pts[j * 2], za = pts[j * 2 + 1], xb = pts[jb * 2], zb = pts[jb * 2 + 1];
      let wx = zb - za, wz = -(xb - xa); const L = Math.hypot(wx, wz) || 1; wx /= L; wz /= L;
      if (wx * ((xa + xb) / 2 - cx) + wz * ((za + zb) / 2 - cz) < 0) { wx = -wx; wz = -wz; }
      const a0 = put(xa, 0, za, wx, 0, wz, along, 0), a1 = put(xb, 0, zb, wx, 0, wz, along + L, 0);
      const a2 = put(xb, h, zb, wx, 0, wz, along + L, 1), a3 = put(xa, h, za, wx, 0, wz, along, 1);
      idx[t++] = a0; idx[t++] = a1; idx[t++] = a2; idx[t++] = a0; idx[t++] = a2; idx[t++] = a3;
      along += L;
    }
    const r0 = v;
    for (let j = 0; j < k; j++) put(pts[j * 2], h, pts[j * 2 + 1], 0, 1, 0, 0, -1);
    for (let j = 1; j < k - 1; j++) { idx[t++] = r0; idx[t++] = r0 + j; idx[t++] = r0 + j + 1; }
  }

  // ---- official zones (taxi zones / neighbourhoods): outer ring of each, for the demand-and-traffic columns
  const rx = q(S.ring_x).map((x) => x - ox), rz = q(S.ring_z).map((z) => z - oz);
  const zones = S.zones.map((zn, i) => {
    const rings: number[][] = [];
    for (let r = S.zone_ring_off[i]; r < S.zone_ring_off[i + 1]; r++) {
      const ring: number[] = [];
      for (let p = S.ring_off[r]; p < S.ring_off[r + 1]; p++) ring.push(rx[p], rz[p]);
      if (ring.length >= 6) rings.push(ring);
    }
    let cx = 0, cz = 0, n = 0;
    for (const ring of rings) for (let p = 0; p < ring.length; p += 2) { cx += ring[p]; cz += ring[p + 1]; n++; }
    return { id: zn.id, name: zn.name, rings, cx: n ? cx / n : 0, cz: n ? cz / n : 0 };
  });

  const segs = new Float32Array(seg), maj = new Float32Array(major);
  const geo: CityGeo = {
    city, label: S.label, short: S.short, WX: S.WX, WZ: S.WZ, unitM: S.unit_m, nBuildings: B.n, nNodes: S.n_nodes, tallest: tallest * S.unit_m,
    buildingSource: B.source,
    buildings: { pos, nor, uv, info, idx: idx.subarray(0, t) },
    streets: { pos: segs, major: maj },
    graph: { nx, nz, src, dst, cls, geoOff, gx, gz, classSpeed: S.class_speed },
    zones,
  };
  return { geo, transfer: [pos.buffer, nor.buffer, uv.buffer, info.buffer, idx.buffer, segs.buffer, maj.buffer, nx.buffer, nz.buffer, gx.buffer, gz.buffer] };
}

const ctx = self as unknown as { postMessage(m: unknown, t?: Transferable[]): void; onmessage: ((e: MessageEvent) => void) | null };
ctx.onmessage = async (e: MessageEvent<{ id: number; city: string; base: string }>) => {
  try {
    const { geo, transfer } = await build(e.data.city, e.data.base);
    ctx.postMessage({ id: e.data.id, geo }, transfer);
  } catch (err) {
    ctx.postMessage({ id: e.data.id, error: String(err) });
  }
};
