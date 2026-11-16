import { motion } from 'framer-motion';
import {
  ArrowRight, Activity, Clock3, Database, Gauge, Map as MapIcon, Play, Radar, Route, Satellite, ShieldCheck, Sparkles, Users, Workflow, Scale,
} from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import HeroMap from './components/HeroMap';
import { BenchmarkChart, FullDayChart, HourlyChart, RealEtaCard } from './components/Charts';
import { CITIES, fmt, mmss } from './data';
import { CityProvider } from './city';
import { useCity } from './cityContext';

const appUrl = (city: string) => `/app?city=${city}`;
const pct = (v: number) => `${Math.round(v * 100)}%`;
const pctRange = (ci?: [number, number]) => (ci ? `${Math.round(ci[0] * 100)}–${Math.round(ci[1] * 100)}%` : '');
const pFmt = (p: number) => (p < 0.001 ? '< 0.001' : `= ${p.toFixed(3)}`);

type Health = { mode: string; fleet: number; demand: string; city?: string; city_label?: string } | null;
function useHealth(): Health | 'offline' | 'loading' {
  const [h, setH] = useState<Health | 'offline' | 'loading'>('loading');
  useEffect(() => {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 2000);
    fetch('/api/health', { signal: ctl.signal, cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setH)
      .catch(() => setH('offline'))
      .finally(() => clearTimeout(to));
    return () => ctl.abort();
  }, []);
  return h;
}

const Reveal = ({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) => (
  <motion.div className={className} initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: '-60px' }}
    transition={{ duration: 0.7, delay, ease: [0.2, 0.8, 0.2, 1] }}>{children}</motion.div>
);

function Wordmark() {
  return (
    <a href="/" className="wide flex items-center text-[26px] font-extrabold leading-none tracking-tight" aria-label="ORCA home">
      ORCA<span className="ml-1 inline-block h-[0.42em] w-[0.42em] rounded-full bg-taxi shadow-[inset_0_0_0_2px_#0B0C0E]" />
    </a>
  );
}

function CitySwitch({ compact = false }: { compact?: boolean }) {
  const { city, setCity } = useCity();
  if (CITIES.length < 2) return null;
  return (
    <div role="radiogroup" aria-label="City" className={`inline-flex rounded-full bg-white p-1 shadow-[0_0_0_1px_#DADDE1] ${compact ? 'text-[13px]' : 'text-[14px]'}`}>
      {CITIES.map((c) => (
        <button key={c.name} type="button" role="radio" aria-checked={c.name === city.name} onClick={() => setCity(c.name)}
          className={`rounded-full px-3.5 py-1.5 font-semibold transition ${c.name === city.name ? 'bg-ink text-white' : 'text-ink-2 hover:text-ink'}`}>
          {c.short}
        </button>
      ))}
    </div>
  );
}

function Nav() {
  const { city } = useCity();
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 12);
    on(); window.addEventListener('scroll', on, { passive: true });
    return () => window.removeEventListener('scroll', on);
  }, []);
  return (
    <header className={`sticky top-0 z-40 transition-colors duration-300 ${scrolled ? 'bg-paper/85 shadow-[0_1px_0_#DADDE1] backdrop-blur-md' : ''}`}>
      <nav className="mx-auto flex max-w-page items-center justify-between gap-4 px-5 py-4 md:px-8" aria-label="Main">
        <div className="flex items-center gap-5"><Wordmark /><div className="hidden sm:block"><CitySwitch compact /></div></div>
        <div className="hidden items-center gap-7 text-[15px] font-medium text-ink-2 lg:flex">
          <a className="hover:text-ink" href="#how">How it works</a>
          <a className="hover:text-ink" href="#results">Results</a>
          <a className="hover:text-ink" href="#reality">Reality check</a>
          <a className="hover:text-ink" href="#data">Real data</a>
          <a className="hover:text-ink" href="#tour">Tour</a>
        </div>
        <a href={appUrl(city.name)} className="btn-primary !px-4 !py-2.5 text-[14px]">Open control room <ArrowRight size={16} /></a>
      </nav>
    </header>
  );
}

function LiveBadge() {
  const h = useHealth();
  const engine = h !== 'loading' && h !== 'offline' && h?.mode === 'live' ? h : null;
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-white px-3.5 py-1.5 text-[13px] font-medium text-ink-2 shadow-[0_0_0_1px_#DADDE1]">
      <span className="relative flex h-2 w-2">
        {engine && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${engine ? 'bg-emerald-500' : 'bg-slate'}`} />
      </span>
      {h === 'loading' ? 'Checking engine…'
        : engine ? `Engine live · ${engine.city_label?.split(' street')[0] ?? ''} · ${fmt(engine.fleet)} cars · ${engine.demand}`
          : `Runs in your browser on real ${CITIES.map((c) => c.short).join(' and ')} data`}
    </span>
  );
}

function Hero() {
  const { city } = useCity();
  const b = city.bench;
  return (
    <section className="relative overflow-hidden">
      <div className="grid-bg pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative mx-auto grid max-w-page items-center gap-12 px-5 pb-20 pt-10 md:grid-cols-[1.1fr_1fr] md:px-8 md:pb-28 md:pt-16">
        <div>
          <Reveal><LiveBadge /></Reveal>
          <Reveal delay={0.05}>
            <h1 className="wide mt-6 text-balance text-[40px] font-extrabold leading-[1.0] tracking-tight sm:text-[52px] lg:text-[60px]">
              The right car for <span className="relative whitespace-nowrap"><span className="relative z-10">every ride</span>
                <span className="absolute inset-x-0 bottom-[0.08em] -z-0 h-[0.32em] bg-taxi" aria-hidden /></span>, every 3 seconds.
            </h1>
          </Reveal>
          <Reveal delay={0.1}>
            <p className="mt-6 max-w-[34rem] text-[18px] leading-relaxed text-ink-2">
              ORCA is a real-time dispatch engine for ride-hailing fleets. It scores every car for every rider on drive time by road,
              acceptance, GPS health and upcoming demand, then solves the whole city at once. It runs on the real streets, buildings and riders
              of {CITIES.map((c) => c.short).join(' and ')}
              {b && <> and, in {city.short}, cuts the average wait by <b className="text-ink">{pct(b.cut('steady'))}</b> against the standard nearest-car rule</>}.
            </p>
          </Reveal>
          <Reveal delay={0.15} className="mt-8 flex flex-wrap items-center gap-3">
            <a href={appUrl(city.name)} className="btn-taxi text-[16px]">Launch the {city.short} control room <ArrowRight size={18} /></a>
            <a href="#tour" className="btn-ghost text-[16px]"><Play size={16} fill="currentColor" /> Watch the tour</a>
          </Reveal>
          <Reveal delay={0.18} className="mt-5 sm:hidden"><CitySwitch /></Reveal>
          <Reveal delay={0.2}>
            <dl className="mt-12 grid max-w-[34rem] grid-cols-3 gap-6 border-t border-line pt-6">
              {[
                ...(b ? [[`${Math.round(b.agg('steady', 'orca_learned', 'mean'))} s`, 'mean wait with ORCA'],
                  [`${Math.round(b.agg('steady', 'nearest', 'mean'))} s`, 'with nearest car']] : []),
                [fmt(city.facts?.intersections ?? 0), `real ${city.short} intersections`],
                ...(b ? [] : [[fmt(city.facts?.buildings ?? 0), 'real buildings'], [String(city.facts?.zones ?? 0), 'official zones']]),
              ].map(([v, l]) => (
                <div key={l}><dt className="sr-only">{l}</dt><dd className="num wide text-[30px] font-extrabold leading-none">{v}</dd>
                  <p className="mt-2 text-[13px] leading-snug text-ink-3">{l}</p></div>
              ))}
            </dl>
          </Reveal>
        </div>
        <Reveal delay={0.1} className="relative mx-auto aspect-square w-full max-w-[520px]">
          <div className="absolute -inset-6 rounded-[40px] bg-taxi/20 blur-3xl" aria-hidden />
          <div className="relative h-full w-full rotate-[-2deg] rounded-[28px] shadow-[0_40px_80px_-40px_rgba(20,30,45,.55)]"><HeroMap /></div>
          <div className="card absolute -bottom-5 -left-3 w-[230px] p-4 text-[13px] sm:-left-8">
            <p className="font-bold">Why this car?</p>
            <p className="mt-1 text-ink-2">In the control room, click any rider to see every candidate car ORCA scored and why it picked one.</p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

const signals = [
  { icon: Route, t: 'Drive time by road', b: 'A gradient-boosted ETA model on top of the fastest path through the real street network.' },
  { icon: Users, t: 'Will the driver accept?', b: 'A per-driver Beta-Bernoulli estimate learned online from every offer and decline.' },
  { icon: Satellite, t: 'Is the GPS fresh?', b: 'Cars that went silent are not offered rides they cannot hear about.' },
  { icon: Radar, t: 'What is coming next', b: 'A Poisson demand forecaster over official zones keeps the last spare car where it will be needed.' },
];

function How() {
  return (
    <section id="how" className="scroll-mt-20 border-y border-line bg-white">
      <div className="mx-auto max-w-page px-5 py-24 md:px-8">
        <Reveal><p className="eyebrow">How it works</p>
          <h2 className="wide mt-3 max-w-3xl text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[48px]">
            Four signals per car, one optimal match for the whole city.</h2></Reveal>
        <div className="mt-14 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {signals.map((s, i) => (
            <Reveal key={s.t} delay={i * 0.06} className="card group p-6 transition hover:-translate-y-1">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-taxi shadow-[inset_0_0_0_1.5px_#0B0C0E]"><s.icon size={20} /></span>
              <h3 className="mt-5 text-[18px] font-bold">{s.t}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-ink-2">{s.b}</p>
            </Reveal>
          ))}
        </div>
        <div className="mt-16 grid gap-10 md:grid-cols-3">
          {[
            { icon: Clock3, t: 'Batch every 3 seconds', b: 'Requests are gathered briefly and solved together with the Hungarian algorithm, so one rider’s best car is not taken by someone with other good options.' },
            { icon: Workflow, t: 'Rebalance, and never let a rider time out', b: 'Idle cars drift toward zones forecast to run short, and a give-up guard prefers a car that arrives in time for riders who have waited long.' },
            { icon: ShieldCheck, t: 'A fair, head-to-head test', b: 'ORCA and the nearest-car rule see the same riders, traffic and incidents. Every difference comes from the decisions alone.' },
          ].map((s, i) => (
            <Reveal key={s.t} delay={i * 0.06} className="flex gap-4">
              <s.icon className="mt-1 shrink-0" size={22} />
              <div><h3 className="text-[17px] font-bold">{s.t}</h3><p className="mt-1.5 text-[15px] leading-relaxed text-ink-2">{s.b}</p></div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function Results() {
  const { city } = useCity();
  const b = city.bench;
  return (
    <section id="results" className="scroll-mt-20">
      <div className="mx-auto max-w-page px-5 py-24 md:px-8">
        <Reveal className="flex flex-wrap items-end justify-between gap-4">
          <p className="eyebrow">Results · {city.long}</p><CitySwitch compact />
        </Reveal>
        {!b ? (
          <Reveal><p className="mt-6 text-[17px] text-ink-2">The {city.short} benchmark has not been run yet (python -m orca.bench --city {city.name}).</p></Reveal>
        ) : (
          <>
            <Reveal>
              <h2 className="wide mt-3 max-w-3xl text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[48px]">
                {pct(b.cut('steady'))} shorter waits on an ordinary day. {pct(b.cut('stress'))} when the city breaks.</h2>
              <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-ink-2">
                {b.config.minutes} simulated minutes per run, {fmt(b.config.fleet)} cars per dispatcher, {b.config.seeds.length} independent seeds, both
                dispatchers on identical riders, traffic and drivers. The stress run injects a demand spike and a GPS outage at minute 20 and a major
                accident at minute 40.{b.config.tuned_weights ? ' ORCA’s scoring weights were tuned on separate seeds the benchmark never uses.' : ''}
              </p>
              {b.cutCI('steady') && (
                <p className="mt-3 text-[14px] text-ink-3">
                  95% confidence intervals over seeds: {pctRange(b.cutCI('steady'))} shorter waits on a steady day, {pctRange(b.cutCI('stress'))} under stress
                  {b.tests?.steady?.learned_vs_nearest != null &&
                    ` (paired Wilcoxon test, p ${pFmt(Math.max(b.tests.steady.learned_vs_nearest, b.tests.stress?.learned_vs_nearest ?? 0))})`}.
                  The dark mark on each bar is the interval of the mean.
                </p>
              )}
            </Reveal>
            <Reveal delay={0.05} className="mt-12"><BenchmarkChart b={b} /></Reveal>
            <Reveal delay={0.05} className="mt-6"><FullDayChart b={b} /></Reveal>
          </>
        )}
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          {[
            city.eta && { icon: Gauge, t: 'Pickup ETA on real streets', n: city.eta.n, unit: 's', what: 'mean absolute error, seconds',
              rows: [[city.eta.heur?.model, city.eta.heur?.mae], ['Gradient boosting (ORCA)', city.eta.ours?.mae]] },
            city.demand && { icon: Activity, t: `Rides per ${city.name === 'manhattan' ? 'taxi zone' : 'neighbourhood'}, next 5 minutes`, n: city.demand.n, unit: '',
              what: 'mean absolute error, rides',
              rows: [[city.demand.naive?.model, city.demand.naive?.mae], [city.demand.base?.model, city.demand.base?.mae], ['Poisson gradient boosting (ORCA)', city.demand.ours?.mae]] },
          ].filter((m): m is NonNullable<typeof m> => Boolean(m)).map((m, i) => (
            <Reveal key={m.t} delay={i * 0.06} className="card p-6">
              <div className="flex items-center gap-3"><m.icon size={20} /><h3 className="text-[17px] font-bold">{m.t}</h3></div>
              <p className="mt-1 text-[13px] text-ink-3">{fmt(m.n)} examples, tested on data later in time than training · {m.what}</p>
              <table className="num mt-4 w-full text-[15px]">
                <tbody>
                  {m.rows.filter(([label]) => label).map(([label, v], k, shown) => (
                    <tr key={k} className={k === shown.length - 1 ? 'font-bold' : 'text-ink-2'}>
                      <td className="border-t border-line py-2.5 pr-3 first-letter:uppercase">{label}</td>
                      <td className="border-t border-line py-2.5 text-right">{v != null ? `${(v as number).toFixed(2)}${m.unit && ' ' + m.unit}` : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function RealityCheck() {
  const nyc = CITIES.find((c) => c.name === 'manhattan');
  const w = nyc?.waits, b = nyc?.bench;
  if (!w || !b) return null;
  const orca = b.agg('steady', 'orca_learned', 'mean'), near = b.agg('steady', 'nearest', 'mean');
  const bars = [
    { label: 'Uber & Lyft, real (Sept 2026)', v: w.mean_on_scene_s, tone: '#2D5BE3' },
    { label: 'Nearest-car rule, simulated', v: near, tone: '#8B929B' },
    { label: 'ORCA, simulated', v: orca, tone: '#FFC20A' },
  ];
  const max = Math.max(...bars.map((x) => x.v)) * 1.1;
  return (
    <section id="reality" className="scroll-mt-20 border-t border-line">
      <div className="mx-auto grid max-w-page gap-12 px-5 py-24 md:grid-cols-[0.9fr_1.1fr] md:px-8">
        <Reveal>
          <p className="eyebrow">Reality check</p>
          <h2 className="wide mt-3 text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[44px]">How long do real riders wait?</h2>
          <p className="mt-5 text-[17px] leading-relaxed text-ink-2">
            The TLC records, for every Uber and Lyft trip, when the rider asked and when the driver arrived. Across {fmt(w.trips)} Manhattan rides
            in September 2026, the driver was on scene after {mmss(w.mean_on_scene_s)} on average ({mmss(w.on_scene_p90)} for the slowest
            tenth). That anchors the simulation: its waits are the same order of magnitude as reality.
          </p>
          <p className="mt-4 flex items-start gap-2 rounded-xl bg-sky/70 p-4 text-[14px] leading-relaxed text-ink-2">
            <Scale size={18} className="mt-0.5 shrink-0" />
            <span>Not a like-for-like race: the platforms run thousands of drivers and pool rides, the simulation runs {fmt(b.config.fleet)} cars per
              dispatcher on a sampled share of the demand. The comparison that matters is ORCA against the nearest-car rule on identical riders.</span>
          </p>
        </Reveal>
        <Reveal delay={0.08} className="card p-6">
          <p className="text-[17px] font-bold">Mean wait, request to car at the rider · Manhattan, weekday</p>
          <div className="mt-6 space-y-5">
            {bars.map((x, i) => (
              <div key={x.label}>
                <div className="mb-1.5 flex justify-between text-[14px]"><span className="text-ink-2">{x.label}</span><b className="num">{mmss(x.v)}</b></div>
                <div className="h-3 overflow-hidden rounded-full bg-sky">
                  <motion.div className="h-full rounded-full" style={{ background: x.tone }} initial={{ width: 0 }}
                    whileInView={{ width: `${(x.v / max) * 100}%` }} viewport={{ once: true }} transition={{ duration: 0.9, delay: i * 0.1 }} />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-5 text-[13px] text-ink-3">Real: {w.source}. Simulated: steady-demand benchmark, {b.config.seeds.length} seeds.</p>
        </Reveal>
      </div>
    </section>
  );
}

function RealData() {
  const { city } = useCity();
  const f = city.facts, t = city.traffic, cal = city.calibration;
  return (
    <section id="data" className="scroll-mt-20 border-y border-line bg-white">
      <div className="mx-auto grid max-w-page gap-12 px-5 py-24 md:grid-cols-[0.9fr_1.1fr] md:px-8">
        <Reveal>
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="eyebrow">Real data · {city.short}</p><CitySwitch compact /></div>
          <h2 className="wide mt-3 text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[44px]">Real streets, real buildings, real riders.</h2>
          <p className="mt-5 text-[17px] leading-relaxed text-ink-2">
            Cars drive {city.short}’s actual street network from OpenStreetMap, one-way rules included, past {fmt(f?.buildings ?? 0)} buildings from
            the city’s official footprints, each extruded from its real outline. Riders come from {city.dataLabel} trip records, and street speeds
            come from those trips too, per zone and per hour{cal ? <>; fitted to real trip times, the median simulated trip matches the median real one
            ({mmss(cal.median_real_s)})</> : null}.
          </p>
          <dl className="mt-8 grid grid-cols-2 gap-x-6 gap-y-7">
            {[
              ...(t ? [[fmt(t.kept_trips), `weekday trips, ${t.days} days`]] : []),
              [`${fmt(f?.street_km ?? 0)} km`, `of drivable street, ${Math.round((f?.one_way_share ?? 0) * 100)}% one-way`],
              [fmt(f?.buildings ?? 0), `buildings, tallest ${f?.tallest_m ?? 0} m`],
              [String(f?.zones ?? 0), city.name === 'manhattan' ? 'official taxi zones' : 'official neighbourhoods'],
            ].map(([v, l]) => (
              <div key={l}><dd className="num wide text-[28px] font-extrabold leading-none">{v}</dd><dt className="mt-2 text-[14px] leading-snug text-ink-3">{l}</dt></div>
            ))}
          </dl>
          {t?.services && (
            <p className="mt-6 text-[14px] text-ink-3">Services replayed: {Object.entries(t.services).map(([k, v]) => `${k} ${fmt(v)}`).join(' · ')}</p>
          )}
          <p className="mt-6 flex items-start gap-2 rounded-xl bg-sky/70 p-4 text-[14px] leading-relaxed text-ink-2">
            <Database size={18} className="mt-0.5 shrink-0" />
            <span>What is still simulated: individual car movement along the streets and driver behaviour (acceptance, response time). No public
              dataset has live GPS traces. Trip records give zones or tract centres rather than exact points.</span>
          </p>
        </Reveal>
        <div className="space-y-6"><Reveal delay={0.08}><HourlyChart city={city} /></Reveal><Reveal delay={0.1}><RealEtaCard city={city} /></Reveal></div>
      </div>
    </section>
  );
}

function Tour() {
  return (
    <section id="tour" className="scroll-mt-20">
      <div className="mx-auto max-w-page px-5 py-24 md:px-8">
        <Reveal className="flex flex-wrap items-end justify-between gap-6">
          <div><p className="eyebrow">The tour</p>
            <h2 className="wide mt-3 text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[48px]">See it work in 100 seconds.</h2></div>
          <p className="max-w-md text-[16px] text-ink-2">Narrated, with sound, recorded straight from the control room’s guided tour in Manhattan. Turn your sound on.</p>
        </Reveal>
        <Reveal delay={0.05} className="mt-10 overflow-hidden rounded-[24px] bg-ink shadow-[0_40px_80px_-40px_rgba(20,30,45,.6)]">
          <video className="aspect-video w-full" controls preload="none" playsInline poster="/media/orca-tour-poster.jpg">
            <source src="/media/orca-tour.webm" type='video/webm; codecs="vp9, opus"' />
            <source src="/media/orca-tour.mp4" type="video/mp4" />
            <track kind="captions" src="/media/orca-tour.vtt" srcLang="en" label="English" default />
          </video>
        </Reveal>
      </div>
    </section>
  );
}

function Stack() {
  const { city } = useCity();
  const total = (k: 'intersections' | 'buildings') => CITIES.reduce((a, c) => a + (c.facts?.[k] ?? 0), 0);
  const items = [
    ['Python 3.12 · Starlette · Uvicorn', 'async API, Server-Sent Events at 10 Hz, Prometheus metrics, hardened middleware'],
    ['scikit-learn · SciPy · NumPy · pandas', 'gradient-boosted ETA and demand models, Hungarian matching, shortest paths'],
    ['Apache Arrow / Parquet', 'ingest of 20M+ real trips a month'],
    ['OpenStreetMap · city open data', `${fmt(total('intersections'))} intersections and ${fmt(total('buildings'))} building footprints in ${CITIES.length} cities, own map projections`],
    ['Hand-written WebGL2', 'extruded real footprints, day and night lighting, camera-following shadow map, up to 4,000 cars'],
    ['React · TypeScript · Vite · Tailwind', 'this home page'],
    ['GitHub Actions · Docker · Playwright', 'lint, unit, real-data, browser and visual-regression tests on every push'],
  ];
  return (
    <section className="border-t border-line bg-ink text-white">
      <div className="mx-auto max-w-page px-5 py-24 md:px-8">
        <Reveal><p className="eyebrow !text-white/50">Under the hood</p>
          <h2 className="wide mt-3 max-w-3xl text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[48px]">Production-shaped, end to end.</h2></Reveal>
        <div className="mt-12 grid gap-px overflow-hidden rounded-2xl bg-white/10 sm:grid-cols-2 lg:grid-cols-3">
          {items.map(([t, b], i) => (
            <Reveal key={t} delay={i * 0.04} className="bg-ink p-6">
              <p className="text-[16px] font-bold">{t}</p><p className="mt-1.5 text-[14px] text-white/60">{b}</p>
            </Reveal>
          ))}
        </div>
        <Reveal className="mt-16 flex flex-col items-start justify-between gap-6 rounded-3xl bg-taxi p-8 text-ink md:flex-row md:items-center md:p-10">
          <div><h3 className="wide text-[28px] font-extrabold leading-tight md:text-[34px]">Run the city yourself.</h3>
            <p className="mt-2 max-w-xl text-[16px] text-ink-2">Pick a city, switch dispatchers, cause outages and accidents, and click any rider to see why ORCA chose that car.</p></div>
          <div className="flex flex-wrap gap-3">
            {CITIES.map((c) => (
              <a key={c.name} href={appUrl(c.name)} className={c.name === city.name ? 'btn-primary text-[16px]' : 'btn-ghost text-[16px]'}>
                <MapIcon size={18} /> {c.short}
              </a>
            ))}
          </div>
        </Reveal>
      </div>
      <footer className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-4 border-t border-white/10 px-5 py-8 text-[13px] text-white/50 md:px-8">
        <span className="flex items-center gap-2"><Sparkles size={14} /> ORCA real-time dispatch engine</span>
        <span>Streets © OpenStreetMap contributors. {CITIES.map((c) => `${c.short}: ${c.credit}`).join(' ')} Sound: original score.</span>
      </footer>
    </section>
  );
}

export default function App() {
  const initial = new URLSearchParams(location.search).get('city');
  const [name, setName] = useState(CITIES.some((c) => c.name === initial) ? (initial as string) : CITIES[0]?.name);
  const value = useMemo(() => ({
    city: CITIES.find((c) => c.name === name) ?? CITIES[0],
    setCity: (n: string) => {
      setName(n);
      const u = new URL(location.href); u.searchParams.set('city', n); history.replaceState(null, '', u);
    },
  }), [name]);
  if (!CITIES.length) return <p className="p-8">No city has been built yet: run scripts/build_streets.py.</p>;
  return (
    <CityProvider value={value}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:p-2">Skip to content</a>
      <Nav />
      <main id="main">
        <Hero />
        <How />
        <Results />
        <RealityCheck />
        <RealData />
        <Tour />
        <Stack />
      </main>
    </CityProvider>
  );
}
