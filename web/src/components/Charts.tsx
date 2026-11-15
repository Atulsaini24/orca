import { motion } from 'framer-motion';
import { agg, policies, realData, scenarios } from '../data';

/** Mean and p95 wait for each dispatcher, per scenario, from docs/benchmark.json. */
export function BenchmarkChart() {
  const max = Math.max(...scenarios.flatMap((s) => policies.map((p) => agg(s.key, p.key, 'p95'))));
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
              const mean = agg(s.key, p.key, 'mean'), p95 = agg(s.key, p.key, 'p95');
              return (
                <div key={p.key}>
                  <div className="mb-1.5 flex items-baseline justify-between text-[14px]">
                    <span className={p.key === 'orca_learned' ? 'font-bold' : 'text-ink-2'}>{p.label}</span>
                    <span className="num"><b className="text-[17px]">{Math.round(mean)} s</b>
                      <span className="text-ink-3"> mean · {Math.round(p95)} s p95</span></span>
                  </div>
                  <div className="relative h-3 overflow-hidden rounded-full bg-sky">
                    <motion.div className="absolute inset-y-0 left-0 rounded-full opacity-35" style={{ background: p.tone }}
                      initial={{ width: 0 }} whileInView={{ width: `${(p95 / max) * 100}%` }} viewport={{ once: true }}
                      transition={{ duration: 1, delay: i * 0.1, ease: [0.2, 0.8, 0.2, 1] }} />
                    <motion.div className="absolute inset-y-0 left-0 rounded-full" style={{ background: p.tone, boxShadow: p.key === 'orca_learned' ? 'inset 0 0 0 1.5px #0B0C0E' : undefined }}
                      initial={{ width: 0 }} whileInView={{ width: `${(mean / max) * 100}%` }} viewport={{ once: true }}
                      transition={{ duration: 1, delay: 0.15 + i * 0.1, ease: [0.2, 0.8, 0.2, 1] }} />
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-5 text-[13px] text-ink-3">
            Riders who gave up: {policies.map((p) => `${p.label.replace(', ', ' ')} ${Math.round(agg(s.key, p.key, 'lost'))}`).join(' · ')}
          </p>
        </figure>
      ))}
    </div>
  );
}

/** Real NYC trips per hour (bars) and real median taxi speed per hour (line). */
export function HourlyChart() {
  const W = 720, H = 240, pad = { l: 44, r: 44, t: 16, b: 30 };
  const tph = realData.trips_per_hour, spd = realData.speed_mps.map((v) => v * 3.6);
  const maxT = Math.max(...tph), maxS = Math.ceil(Math.max(...spd) / 5) * 5;
  const bw = (W - pad.l - pad.r) / 24;
  const x = (h: number) => pad.l + h * bw + bw / 2;
  const yT = (v: number) => H - pad.b - (v / maxT) * (H - pad.t - pad.b);
  const yS = (v: number) => H - pad.b - (v / maxS) * (H - pad.t - pad.b);
  const line = spd.map((v, h) => `${h ? 'L' : 'M'}${x(h).toFixed(1)} ${yS(v).toFixed(1)}`).join(' ');
  return (
    <figure className="card p-6">
      <figcaption className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <span className="text-[17px] font-bold">A real Manhattan weekday, hour by hour</span>
        <span className="flex gap-4 text-[13px] text-ink-2">
          <span className="flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm bg-taxi shadow-[inset_0_0_0_1px_#0B0C0E]" />taxi pickups per hour</span>
          <span className="flex items-center gap-1.5"><i className="inline-block h-0.5 w-4 bg-signal-blue" />median taxi speed, km/h</span>
        </span>
      </figcaption>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="min-w-[560px]" role="img"
          aria-label={`Bar chart of real taxi pickups per hour, peaking at about ${Math.round(maxT / 1000)} thousand, with median speed falling from ${Math.round(Math.max(...spd))} km/h at night to ${Math.round(Math.min(...spd))} km/h at midday.`}>
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <line key={f} x1={pad.l} x2={W - pad.r} y1={yT(maxT * f)} y2={yT(maxT * f)} stroke="#DADDE1" strokeDasharray="3 4" />
          ))}
          {tph.map((v, h) => (
            <motion.rect key={h} x={pad.l + h * bw + 3} width={bw - 6} rx={3} fill="#FFC20A" stroke="#0B0C0E" strokeWidth={1}
              initial={{ y: H - pad.b, height: 0 }} whileInView={{ y: yT(v), height: H - pad.b - yT(v) }} viewport={{ once: true }}
              transition={{ duration: 0.8, delay: h * 0.025 }}><title>{`${h}:00 · ${Math.round(v).toLocaleString()} pickups · ${spd[h].toFixed(1)} km/h`}</title></motion.rect>
          ))}
          <motion.path d={line} fill="none" stroke="#2D5BE3" strokeWidth={2.5} strokeLinejoin="round"
            initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.4, delay: 0.4 }} />
          {[0, 6, 12, 18, 23].map((h) => (
            <text key={h} x={x(h)} y={H - 10} textAnchor="middle" fontSize={11} fill="#71767E">{`${String(h).padStart(2, '0')}:00`}</text>
          ))}
          <text x={pad.l - 8} y={yT(maxT) + 4} textAnchor="end" fontSize={11} fill="#71767E">{Math.round(maxT / 1000)}k</text>
          <text x={pad.l - 8} y={H - pad.b + 4} textAnchor="end" fontSize={11} fill="#71767E">0</text>
          <text x={W - pad.r + 8} y={yS(maxS) + 4} fontSize={11} fill="#2D5BE3">{maxS}</text>
          <text x={W - pad.r + 8} y={H - pad.b + 4} fontSize={11} fill="#2D5BE3">0</text>
        </svg>
      </div>
      <p className="mt-3 text-[13px] text-ink-3">Source: {realData.source}. Speed is trip distance over trip duration.</p>
    </figure>
  );
}
