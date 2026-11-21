export type Zone = { id: number; name: string; rings: number[][]; cx: number; cz: number };

/** A real city, decoded and centred on its middle (units of 30 m, y up). */
export type CityGeo = {
  city: string; label: string; short: string; WX: number; WZ: number; unitM: number;
  nBuildings: number; nNodes: number; tallest: number; buildingSource: string;
  buildings: { pos: Float32Array; nor: Float32Array; uv: Float32Array; info: Float32Array; idx: Uint32Array };
  streets: { pos: Float32Array; major: Float32Array };
  graph: { nx: Float32Array; nz: Float32Array; src: Uint16Array; dst: Uint16Array; cls: Uint8Array; geoOff: Uint32Array;
    gx: Float32Array; gz: Float32Array; classSpeed: number[] };
  zones: Zone[];
};

let worker: Worker | null = null, seq = 0;
const pending = new Map<number, { ok: (g: CityGeo) => void; fail: (e: Error) => void }>();
const cache = new Map<string, Promise<CityGeo>>();

/** Loads a city's real streets and buildings once per page (decoded in a worker). */
export function loadCity(city: string): Promise<CityGeo> {
  const hit = cache.get(city); if (hit) return hit;
  if (!worker) {
    worker = new Worker(new URL('./city.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (e: MessageEvent<{ id: number; geo?: CityGeo; error?: string }>) => {
      const p = pending.get(e.data.id); if (!p) return; pending.delete(e.data.id);
      if (e.data.geo) p.ok(e.data.geo); else p.fail(new Error(e.data.error ?? 'city failed to load'));
    };
  }
  const id = ++seq, w = worker;
  const pr = new Promise<CityGeo>((ok, fail) => { pending.set(id, { ok, fail }); w.postMessage({ id, city, base: new URL('/app/data', location.href).href }); });
  pr.catch(() => cache.delete(city));
  cache.set(city, pr);
  return pr;
}
