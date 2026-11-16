import { motion } from 'framer-motion';
import { policies, scenarios, type BenchView, type CityData } from '../data';

/** Mean and p95 wait for each dispatcher, per scenario, with 95% confidence intervals over seeds. */
export function BenchmarkChart({ b }: { b: BenchView }) {
  const max = Math.max(...scenarios.flatMap((s) => policies.map((p) => b.agg(s.key, p.key, 'p95'))));
  return (
    <div className="grid gap-6 md:grid-cols-2">
      {scenarios.map((s) => (
        <figure key={s.key} className="card p-6">
          <figcaption className="mb-5 flex items-baseline justify-between gap-4">
            <span className="text-[17px] font-bold">{s.label}</span>
            <span className="text-[13px] text-ink-3">seconds, request to pickup</span>
          </figcaption>
          <div className="space-y-5">
            {policies.map((p, i) => {
              const mean = b.agg(s.key, p.key, 'mean'), p95 = b.agg(s.key, p.key, 'p95');
              const ci = b.ciOf(s.key, p.key, 'mean')?.ci;
              return (
                <div key={p.key}>
                  <div className="mb-1.5 flex items-baseline justify-between text-[14px]">
                    <span className={p.key === 'orca_learned' ? 'font-bold' : 'text-ink-2'}>{p.label}</span>
                    <span className="num" title={ci ? `95% confidence interval over seeds: ${Math.round(ci[0])}–${Math.round(ci[1])} s` : undefined}>
                      <b className="text-[17px]">{Math.round(mean)} s</b>{ci && <span className="text-ink-3"> ±{Math.round((ci[1] - ci[0]) / 2)}</span>}
                      <span className="text-ink-3"> mean · {Math.round(p95)} s p95</span></span>
                  </div>
                  <div className="relative h-3 overflow-hidden rounded-full bg-sky">
                    <motion.div className="absolute inset-y-0 left-0 rounded-full opacity-35" style={{ background: p.tone }}
                      initial={{ width: 0 }} whileInView={{ width: `${(p95 / max) * 100}%` }} viewport={{ once: true }}
                      transition={{ duration: 1, delay: i * 0.1, ease: [0.2, 0.8, 0.2, 1] }} />
                    <motion.div className="absolute inset-y-0 left-0 rounded-full" style={{ background: p.tone, boxShadow: p.key === 'orca_learned' ? 'inset 0 0 0 1.5px #0B0C0E' : undefined }}
                      initial={{ width: 0 }} whileInView={{ width: `${(mean / max) * 100}%` }} viewport={{ once: true }}
                      transition={{ duration: 1, delay: 0.15 + i * 0.1, ease: [0.2, 0.8, 0.2, 1] }} />
                    {ci && <span className="absolute rounded-full bg-ink/70" aria-hidden
                      style={{ left: `${(ci[0] / max) * 100}%`, width: `max(2px, ${((ci[1] - ci[0]) / max) * 100}%)`, top: '40%', bottom: '40%' }} />}
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-5 text-[13px] text-ink-3">
            Riders who gave up: {policies.map((p) => `${p.label.replace(', ', ' ')} ${b.agg(s.key, p.key, 'lost').toFixed(1)}`).join(' · ')}
            {b.has('orca_no_guard') && ` · without the give-up guard ${b.agg(s.key, 'orca_no_guard', 'lost').toFixed(1)}`}
          </p>
        </figure>
      ))}
    </div>
  );
}

/** Real trips per hour (bars) and real median speed per hour (line), for one city. */
export function HourlyChart({ city }: { city: CityData }) {
  const t = city.traffic;
  if (!t) return null;
  const W = 720, H = 240, pad = { l: 44, r: 44, t: 16, b: 30 };
  const tph = t.trips_per_hour, spd = t.speed_mps.map((v) => v * 3.6);
  const maxT = Math.max(...tph), maxS = Math.ceil(Math.max(...spd) / 5) * 5;
  const bw = (W - pad.l - pad.r) / 24;
  const x = (h: number) => pad.l + h * bw + bw / 2;
  const yT = (v: number) => H - pad.b - (v / maxT) * (H - pad.t - pad.b);
  const yS = (v: number) => H - pad.b - (v / maxS) * (H - pad.t - pad.b);
  const line = spd.map((v, h) => `${h ? 'L' : 'M'}${x(h).toFixed(1)} ${yS(v).toFixed(1)}`).join(' ');
  const k = maxT >= 1000 ? `${Math.round(maxT / 1000)}k` : String(Math.round(maxT));
  return (
    <figure className="card p-6">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <span className="text-[17px] font-bold">A real {city.short} weekday, hour by hour</span>
        <span className="flex gap-4 text-[13px] text-ink-2">
          <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-taxi shadow-[inset_0_0_0_1px_#0B0C0E]" />pickups per hour</span>
          <span className="flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 bg-signal-blue" />median speed, km/h</span>
        </span>
      </figcaption>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="min-w-[560px]" role="img"
          aria-label={`Bar chart of real pickups per hour in ${city.short}, peaking at about ${k}, with median speed between ${Math.round(Math.min(...spd))} and ${Math.round(Math.max(...spd))} km/h.`}>
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <line key={f} x1={pad.l} x2={W - pad.r} y1={yT(maxT * f)} y2={yT(maxT * f)} stroke="#DADDE1" strokeDasharray="3 4" />
          ))}
          {tph.map((v, h) => (
            <motion.rect key={`${city.name}-${h}`} x={pad.l + h * bw + 3} width={bw - 6} rx={3} fill="#FFC20A" stroke="#0B0C0E" strokeWidth={1}
              initial={{ y: H - pad.b, height: 0 }} animate={{ y: yT(v), height: H - pad.b - yT(v) }}
              transition={{ duration: 0.6, delay: h * 0.015 }}><title>{`${h}:00 · ${Math.round(v).toLocaleString()} pickups · ${spd[h].toFixed(1)} km/h`}</title></motion.rect>
          ))}
          <path d={line} fill="none" stroke="#2D5BE3" strokeWidth={2.5} strokeLinejoin="round" />
          {[0, 6, 12, 18, 23].map((h) => (
            <text key={h} x={x(h)} y={H - 10} textAnchor="middle" fontSize={11} fill="#71767E">{`${String(h).padStart(2, '0')}:00`}</text>
          ))}
          <text x={pad.l - 8} y={yT(maxT) + 4} textAnchor="end" fontSize={11} fill="#71767E">{k}</text>
          <text x={pad.l - 8} y={H - pad.b + 4} textAnchor="end" fontSize={11} fill="#71767E">0</text>
          <text x={W - pad.r + 8} y={yS(maxS) + 4} fontSize={11} fill="#2D5BE3">{maxS}</text>
          <text x={W - pad.r + 8} y={H - pad.b + 4} fontSize={11} fill="#2D5BE3">0</text>
        </svg>
      </div>
      <p className="mt-3 text-[13px] text-ink-3">Source: {t.source}. Speed is trip distance over trip duration.</p>
    </figure>
  );
}

/** Mean wait by hour over a simulated 24-hour day on the real demand and traffic profile. */
export function FullDayChart({ b }: { b: BenchView }) {
  const fullDay = b.fullDay;
  if (!fullDay) return null;
  const W = 720, H = 230, pad = { l: 44, r: 16, t: 14, b: 30 };
  const vals = [...fullDay.orca, ...fullDay.nearest].filter((v): v is number => v != null);
  const max = Math.ceil(Math.max(...vals) / 30) * 30;
  const x = (h: number) => pad.l + (h / 23) * (W - pad.l - pad.r);
  const y = (v: number) => H - pad.b - (v / max) * (H - pad.t - pad.b);
  const path = (arr: (number | null)[]) =>
    arr.map((v, h) => (v == null ? '' : `${h && arr[h - 1] != null ? 'L' : 'M'}${x(h).toFixed(1)} ${y(v).toFixed(1)}`)).join(' ');
  const t = fullDay.totals;
  return (
    <figure className="card p-6">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <span className="text-[17px] font-bold">A full day on real demand: mean wait by hour</span>
        <span className="flex gap-4 text-[13px] text-ink-2">
          <span className="flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 bg-slate" />Nearest car</span>
          <span className="flex items-center gap-1.5"><i className="inline-block h-1 w-4 rounded bg-ink" />ORCA</span>
        </span>
      </figcaption>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="min-w-[560px]" role="img"
          aria-label={`Line chart of mean wait by hour over 24 hours: ORCA averages ${Math.round(t.orca.mean)} seconds against ${Math.round(t.nearest.mean)} for nearest-car dispatch.`}>
          {[0.25, 0.5, 0.75, 1].map((f) => <line key={f} x1={pad.l} x2={W - pad.r} y1={y(max * f)} y2={y(max * f)} stroke="#DADDE1" strokeDasharray="3 4" />)}
          <path d={path(fullDay.nearest)} fill="none" stroke="#8B929B" strokeWidth={2.5} />
          <path d={path(fullDay.orca)} fill="none" stroke="#0B0C0E" strokeWidth={2.5} />
          {[0, 6, 12, 18, 23].map((h) => <text key={h} x={x(h)} y={H - 10} textAnchor="middle" fontSize={11} fill="#71767E">{`${String(h).padStart(2, '0')}:00`}</text>)}
          <text x={pad.l - 8} y={y(max) + 4} textAnchor="end" fontSize={11} fill="#71767E">{max}s</text>
          <text x={pad.l - 8} y={y(0) + 4} textAnchor="end" fontSize={11} fill="#71767E">0</text>
        </svg>
      </div>
      <p className="mt-3 text-[13px] text-ink-3">
        24 simulated hours from midnight: {Math.round(t.orca.mean)} s mean wait with ORCA against {Math.round(t.nearest.mean)} s with
        nearest car, over {t.orca.served.toLocaleString()} rides; {t.orca.lost} riders gave up with ORCA, {t.nearest.lost} with nearest car.
      </p>
    </figure>
  );
}

/** Trip-duration model trained on earlier real trips and tested on later ones. */
export function RealEtaCard({ city }: { city: CityData }) {
  const r = city.realEta;
  if (!r) return null;
  const rows = [...r.baselines, r.ours];
  const best = r.baselines.reduce((a, b) => (a.mae < b.mae ? a : b));
  return (
    <figure className="card p-6">
      <figcaption className="text-[17px] font-bold">Validated on real {city.short} trips</figcaption>
      <p className="mt-1 text-[13px] text-ink-3">
        Trip duration, trained on {r.train_days.join(' to ')} and tested on {r.n_test.toLocaleString()} later trips
        ({r.test_days.join(' to ')}) it never saw.
      </p>
      <table className="num mt-4 w-full text-[14px]">
        <thead><tr className="text-left text-[12px] uppercase tracking-wide text-ink-3"><th className="pb-2 font-semibold">Model</th>
          <th className="pb-2 text-right font-semibold">Mean error</th><th className="pb-2 text-right font-semibold">% error</th></tr></thead>
        <tbody>
          {rows.map((m, k) => (
            <tr key={m.model} className={k === rows.length - 1 ? 'font-bold' : 'text-ink-2'}>
              <td className="border-t border-line py-2 pr-3 first-letter:uppercase">{m.model}</td>
              <td className="border-t border-line py-2 text-right">{Math.round(m.mae)} s</td>
              <td className="border-t border-line py-2 text-right">{((m.mape ?? 0) * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[13px] text-ink-3">
        {Math.round((1 - r.ours.mae / best.mae) * 100)}% lower error than the strongest baseline, the historical median for the same
        zone pair and hour. Trip records give zones rather than exact points, which limits how precise any model can be.
      </p>
    </figure>
  );
}
