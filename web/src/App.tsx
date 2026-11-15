import { motion } from 'framer-motion';
import {
  ArrowRight, Activity, Clock3, Database, Gauge, Map as MapIcon, Play, Radar, Route, Satellite, ShieldCheck, Sparkles, Users, Workflow,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import HeroMap from './components/HeroMap';
import { BenchmarkChart, HourlyChart } from './components/Charts';
import { agg, benchConfig, cut, fmt, fmtM, models, realData } from './data';

const APP = '/app';
const pct = (v: number) => `${Math.round(v * 100)}%`;

type Health = { mode: string; fleet: number; demand: string; eta_model: string; forecaster: string } | null;
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

function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 12);
    on(); window.addEventListener('scroll', on, { passive: true });
    return () => window.removeEventListener('scroll', on);
  }, []);
  return (
    <header className={`sticky top-0 z-40 transition-colors duration-300 ${scrolled ? 'bg-paper/85 shadow-[0_1px_0_#DADDE1] backdrop-blur-md' : ''}`}>
      <nav className="mx-auto flex max-w-page items-center justify-between gap-6 px-5 py-4 md:px-8" aria-label="Main">
        <Wordmark />
        <div className="hidden items-center gap-7 text-[15px] font-medium text-ink-2 md:flex">
          <a className="hover:text-ink" href="#how">How it works</a>
          <a className="hover:text-ink" href="#results">Results</a>
          <a className="hover:text-ink" href="#data">Real data</a>
          <a className="hover:text-ink" href="#tour">Tour</a>
        </div>
        <a href={APP} className="btn-primary !px-4 !py-2.5 text-[14px]">Open control room <ArrowRight size={16} /></a>
      </nav>
    </header>
  );
}

function LiveBadge() {
  const h = useHealth();
  const live = h !== 'loading' && h !== 'offline' && h?.mode === 'live';
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-white px-3.5 py-1.5 text-[13px] font-medium text-ink-2 shadow-[0_0_0_1px_#DADDE1]">
      <span className="relative flex h-2 w-2">
        {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${live ? 'bg-emerald-500' : 'bg-slate'}`} />
      </span>
      {h === 'loading' ? 'Checking engine…'
        : live ? `Engine live · ${fmt(h!.fleet)} cars · ${h!.demand}`
          : 'Runs in your browser on real NYC taxi demand'}
    </span>
  );
}

function Hero() {
  const steady = cut('steady');
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
              ORCA is a real-time dispatch engine for ride-hailing fleets. It scores every car for every rider on drive time,
              acceptance, GPS health and upcoming demand, then solves the whole city at once. On real New York taxi demand it
              cuts the average wait by <b className="text-ink">{pct(steady)}</b> against the standard nearest-car rule.
            </p>
          </Reveal>
          <Reveal delay={0.15} className="mt-8 flex flex-wrap gap-3">
            <a href={APP} className="btn-taxi text-[16px]">Launch the live control room <ArrowRight size={18} /></a>
            <a href="#tour" className="btn-ghost text-[16px]"><Play size={16} fill="currentColor" /> Watch the 100-second tour</a>
          </Reveal>
          <Reveal delay={0.2}>
            <dl className="mt-12 grid max-w-[34rem] grid-cols-3 gap-6 border-t border-line pt-6">
              {[
                [`${Math.round(agg('steady', 'orca_learned', 'mean'))} s`, 'mean wait with ORCA'],
                [`${Math.round(agg('steady', 'nearest', 'mean'))} s`, 'with nearest car'],
                [fmtM(realData.raw_trips), 'real NYC trips ingested'],
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
  { icon: Route, t: 'Drive time by road', b: 'A gradient-boosted ETA model fed by live zone speeds, not straight-line distance.' },
  { icon: Users, t: 'Will the driver accept?', b: 'A per-driver Beta-Bernoulli estimate learned online from every offer and decline.' },
  { icon: Satellite, t: 'Is the GPS fresh?', b: 'Cars that went silent are not offered rides they cannot hear about.' },
  { icon: Radar, t: 'What is coming next', b: 'A Poisson demand forecaster keeps the last spare car in a zone about to get busy.' },
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
            { icon: Workflow, t: 'Rebalance between batches', b: 'Idle cars drift toward zones the forecaster says will run short, before the requests arrive.' },
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
  const e = models.eta, d = models.demand;
  return (
    <section id="results" className="scroll-mt-20">
      <div className="mx-auto max-w-page px-5 py-24 md:px-8">
        <Reveal><p className="eyebrow">Results</p>
          <h2 className="wide mt-3 max-w-3xl text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[48px]">
            {pct(cut('steady'))} shorter waits on an ordinary day. {pct(cut('stress'))} when the city breaks.</h2>
          <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-ink-2">
            {benchConfig.minutes} simulated minutes per run, {fmt(benchConfig.fleet)} cars per dispatcher, seeds {benchConfig.seeds.join(' and ')}.
            The stress run injects a demand spike and a GPS outage at minute 20 and a major accident at minute 40.
          </p></Reveal>
        <Reveal delay={0.05} className="mt-12"><BenchmarkChart /></Reveal>
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          {[
            { icon: Gauge, t: 'Pickup ETA', n: e.n, rows: [[e.base?.model, e.base?.mae], [e.heur?.model, e.heur?.mae], ['Gradient boosting (ORCA)', e.ours?.mae]], unit: 's', what: 'mean absolute error, seconds' },
            { icon: Activity, t: 'Rides per zone, next 5 minutes', n: d.n, rows: [[d.naive?.model, d.naive?.mae], [d.base?.model, d.base?.mae], ['Poisson gradient boosting (ORCA)', d.ours?.mae]], unit: '', what: 'mean absolute error, rides' },
          ].map((m, i) => (
            <Reveal key={m.t} delay={i * 0.06} className="card p-6">
              <div className="flex items-center gap-3"><m.icon size={20} /><h3 className="text-[17px] font-bold">{m.t}</h3></div>
              <p className="mt-1 text-[13px] text-ink-3">{fmt(m.n)} examples, tested on data later in time than training · {m.what}</p>
              <table className="num mt-4 w-full text-[15px]">
                <tbody>
                  {m.rows.map(([label, v], k) => (
                    <tr key={k} className={k === m.rows.length - 1 ? 'font-bold' : 'text-ink-2'}>
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

function RealData() {
  return (
    <section id="data" className="scroll-mt-20 border-y border-line bg-white">
      <div className="mx-auto grid max-w-page gap-12 px-5 py-24 md:grid-cols-[0.9fr_1.1fr] md:px-8">
        <Reveal>
          <p className="eyebrow">Real data</p>
          <h2 className="wide mt-3 text-[36px] font-extrabold leading-[1.05] tracking-tight md:text-[44px]">Driven by real New York taxi trips.</h2>
          <p className="mt-5 text-[17px] leading-relaxed text-ink-2">
            Demand is replayed from the official NYC Taxi &amp; Limousine Commission trip records. Each trip is placed inside its real
            pickup and drop-off taxi-zone polygons, and the city’s road speed follows the real median taxi speed for each hour.
          </p>
          <dl className="mt-8 grid grid-cols-2 gap-x-6 gap-y-7">
            {[
              [fmt(realData.raw_trips), 'trips in the January 2015 TLC file'],
              [fmt(realData.kept_trips), 'Manhattan weekday trips kept'],
              [String(realData.zones), 'official taxi-zone polygons'],
              [String(realData.days), 'typical weekdays (holidays and the blizzard removed)'],
            ].map(([v, l]) => (
              <div key={l}><dd className="num wide text-[28px] font-extrabold leading-none">{v}</dd><dt className="mt-2 text-[14px] leading-snug text-ink-3">{l}</dt></div>
            ))}
          </dl>
          <p className="mt-8 flex items-start gap-2 rounded-xl bg-sky/70 p-4 text-[14px] leading-relaxed text-ink-2">
            <Database size={18} className="mt-0.5 shrink-0" />
            <span>What is still simulated: the street grid, individual car movement and driver behaviour. No public dataset has live taxi GPS traces.</span>
          </p>
        </Reveal>
        <Reveal delay={0.08}><HourlyChart /></Reveal>
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
          <p className="max-w-md text-[16px] text-ink-2">Narrated, with sound. Recorded straight from the control room’s guided tour. Turn your sound on.</p>
        </Reveal>
        <Reveal delay={0.05} className="mt-10 overflow-hidden rounded-[24px] bg-ink shadow-[0_40px_80px_-40px_rgba(20,30,45,.6)]">
          <video className="aspect-video w-full" controls preload="metadata" playsInline poster="/media/orca-tour-poster.jpg">
            <source src="/media/orca-tour.mp4" type="video/mp4" />
            <track kind="captions" src="/media/orca-tour.vtt" srcLang="en" label="English" default />
          </video>
        </Reveal>
      </div>
    </section>
  );
}

function Stack() {
  const items = [
    ['Python 3.12 · Starlette · Uvicorn', 'async API, Server-Sent Events at 10 Hz, Prometheus metrics'],
    ['scikit-learn · SciPy · NumPy · pandas', 'gradient-boosted ETA and demand models, Hungarian matching'],
    ['Apache Arrow / Parquet', 'ingest of the 12.7M-row TLC trip file'],
    ['Hand-written WebGL2', '3,400 instanced buildings, shadow-mapped sun, up to 4,000 cars'],
    ['React · TypeScript · Vite · Tailwind', 'this home page'],
    ['Docker · SQLite trip ledger', 'one-command deploy, queryable trip history'],
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
            <p className="mt-2 max-w-xl text-[16px] text-ink-2">Switch dispatchers, cause outages and accidents, and click any rider to see why ORCA chose that car.</p></div>
          <a href={APP} className="btn-primary text-[16px]"><MapIcon size={18} /> Open the control room</a>
        </Reveal>
      </div>
      <footer className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-4 border-t border-white/10 px-5 py-8 text-[13px] text-white/50 md:px-8">
        <span className="flex items-center gap-2"><Sparkles size={14} /> ORCA real-time dispatch engine</span>
        <span>Trip data: NYC Taxi &amp; Limousine Commission. Sound: original score.</span>
      </footer>
    </section>
  );
}

export default function App() {
  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:p-2">Skip to content</a>
      <Nav />
      <main id="main">
        <Hero />
        <How />
        <Results />
        <RealData />
        <Tour />
        <Stack />
      </main>
    </>
  );
}
