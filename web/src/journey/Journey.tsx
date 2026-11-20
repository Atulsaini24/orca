import { motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform, type MotionValue } from 'framer-motion';
import { ArrowDown, ArrowRight } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { CITIES, fmt } from '../data';
import { JourneyEngine, type TrafficTable } from './engine';
import { CHAPTERS, DECIDE, T, chapterAt } from './timeline';
import { loadCity, type CityGeo } from './types';

const appUrl = (city: string) => `/app?city=${city}`;
const NYC = CITIES.find((c) => c.name === 'manhattan');
const TRAFFIC: TrafficTable = (() => {
  const t = NYC?.traffic as unknown as { zone_ids?: number[]; zone_hour_factor?: number[][] } | null;
  return t?.zone_ids && t.zone_hour_factor ? { zoneIds: t.zone_ids, factor: t.zone_hour_factor } : null;
})();
const cut = NYC?.bench ? NYC.bench.cut('steady') : null;

/** Fades a block in and out over a window of the journey, sliding and un-blurring as it comes. */
function Beat({ p, at, children, className = '', from = 'bottom' }: { p: MotionValue<number>; at: readonly [number, number]; children: ReactNode; className?: string; from?: 'bottom' | 'left' | 'right' }) {
  const [a, b] = at, e = Math.min(0.018, (b - a) / 4);
  const opacity = useTransform(p, [a - 0.001, a + e, b - e, b + 0.001], [0, 1, 1, 0]);
  const d = 60, off = useTransform(p, [a, a + e, b - e, b], [d, 0, 0, -d]);
  const blur = useTransform(p, [a, a + e, b - e, b], ['blur(12px)', 'blur(0px)', 'blur(0px)', 'blur(12px)']);
  const neg = useTransform(off, (x) => -x);
  const style = from === 'bottom' ? { opacity, y: off, filter: blur } : { opacity, x: from === 'left' ? neg : off, filter: blur };
  return <motion.div className={`pointer-events-none absolute ${className}`} style={style}>{children}</motion.div>;
}

/** Text that resolves out of random glyphs as it scrolls into place. */
function Scramble({ p, at, text, className = '' }: { p: MotionValue<number>; at: readonly [number, number]; text: string; className?: string }) {
  const [out, setOut] = useState(text);
  const glyphs = '█▓▒░<>/\\|_-=+*#ORCA0123456789';
  useMotionValueEvent(p, 'change', (v) => {
    const k = Math.min(1, Math.max(0, (v - at[0]) / (at[1] - at[0])));
    const n = Math.floor(k * text.length);
    setOut(text.slice(0, n) + text.slice(n).replace(/\S/g, () => glyphs[Math.floor(Math.random() * glyphs.length)]));
  });
  return <span className={className} aria-label={text}>{out}</span>;
}

function Counter({ p, at, to, className = '' }: { p: MotionValue<number>; at: readonly [number, number]; to: number; className?: string }) {
  const v = useTransform(p, [at[0], at[1]], [0, to], { clamp: true });
  const s = useTransform(v, (x) => fmt(x));
  return <motion.span className={className}>{s}</motion.span>;
}

function Title({ p }: { p: MotionValue<number> }) {
  const letters = ['O', 'R', 'C', 'A'];
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center px-5 text-center">
      <p className="sr-only">ORCA: the right car for every ride, every 3 seconds, on the real streets of real cities.</p>
      <div className="flex select-none" aria-hidden>
        {letters.map((l, i) => <Letter key={l} p={p} i={i} l={l} />)}
      </div>
      <Beat p={p} at={[-0.05, 0.05]} className="relative mt-4 max-w-3xl">
        <p className="wide text-[20px] font-bold text-white md:text-[30px]">
          <Scramble p={p} at={[-0.02, 0.0]} text="The right car for every ride, every 3 seconds." />
        </p>
        <p className="mt-3 text-[15px] text-white/60 md:text-[17px]">On the real streets of {CITIES.map((c) => c.short).join(', ')} and Cambridge. Scroll to drive the city.</p>
      </Beat>
    </div>
  );
}
function Letter({ p, i, l }: { p: MotionValue<number>; i: number; l: string }) {
  const y = useTransform(p, [0, 0.06], [0, -260 - i * 140]);
  const rot = useTransform(p, [0, 0.06], [0, (i % 2 ? 1 : -1) * (20 + i * 8)]);
  const o = useTransform(p, [0, 0.045, 0.06], [1, 0.9, 0]);
  return (
    <motion.span style={{ y, rotate: rot, opacity: o }} className="inline-block">
      <motion.span initial={{ y: 120, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ duration: 1, delay: 0.1 + i * 0.08, ease: [0.2, 0.8, 0.2, 1] }}
        className="wide inline-block text-[min(28vw,38vh)] font-extrabold leading-[0.8] tracking-[-0.04em] text-transparent [-webkit-text-stroke:2px_#FFC20A] md:text-[min(19vw,42vh)]">
        {l}
      </motion.span>
    </motion.span>
  );
}

/** Giant city name sliding through the frame, outlined, with the facts underneath. */
function CityCard({ p, at, name, lines }: { p: MotionValue<number>; at: readonly [number, number]; name: string; lines: ReactNode }) {
  const x = useTransform(p, [at[0], at[1]], ['40%', '-55%']);
  return (
    <>
      <motion.div className="pointer-events-none absolute inset-x-0 top-[14%] whitespace-nowrap" style={{ x, opacity: useTransform(p, [at[0], at[0] + 0.02, at[1] - 0.02, at[1]], [0, 1, 1, 0]) }} aria-hidden>
        <span className="wide text-[22vw] font-extrabold uppercase leading-none tracking-tight text-transparent [-webkit-text-stroke:2px_rgba(255,255,255,.85)] md:text-[16vw]">{name}</span>
      </motion.div>
      <Beat p={p} at={[at[0] + 0.02, at[1] - 0.01]} className="bottom-[14%] left-5 right-5 md:left-10 md:max-w-xl">{lines}</Beat>
    </>
  );
}

export default function Journey() {
  const ref = useRef<HTMLElement>(null), canvas = useRef<HTMLCanvasElement>(null), clock = useRef<HTMLSpanElement>(null);
  const engine = useRef<JourneyEngine | null>(null), labels = useRef<Record<string, HTMLElement | null>>({});
  const still = !!useReducedMotion();
  const { scrollYProgress: p } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  const [geo, setGeo] = useState<Record<string, CityGeo>>({});
  const [failed, setFailed] = useState(false);
  const [chapter, setChapter] = useState(0);
  const [cands, setCands] = useState<{ id: string; km: number }[]>([]);
  useMotionValueEvent(p, 'change', (v) => { if (engine.current) engine.current.target = v; setChapter(Math.max(0, chapterAt(v))); });

  useEffect(() => {
    const c = canvas.current, host = ref.current; if (!c || !host) return;
    let e: JourneyEngine;
    try { e = new JourneyEngine(c, { mobile: matchMedia('(max-width: 767px)').matches, still, traffic: TRAFFIC }); } catch { setFailed(true); return; }
    engine.current = e; e.labels = labels.current; e.target = p.get(); e.p = p.get();
    const size = () => e.setSize(c.clientWidth || innerWidth, c.clientHeight || innerHeight);
    size(); const ro = new ResizeObserver(size); ro.observe(c);
    let visible = true, raf = 0, last = performance.now();
    const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting; }, { rootMargin: '100px' }); io.observe(host);
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = (now - last) / 1000; last = now;
      if (!visible) return;
      e.frame(dt);
      if (clock.current) clock.current.textContent = `${String(Math.floor(e.hour)).padStart(2, '0')}:${String(Math.floor((e.hour % 1) * 60)).padStart(2, '0')}`;
    };
    raf = requestAnimationFrame(loop);
    const lost = (ev: Event) => { ev.preventDefault(); setFailed(true); };
    c.addEventListener('webglcontextlost', lost);
    let alive = true;
    (async () => {
      for (const name of ['manhattan', 'chicago', 'cambridge']) {
        try {
          const g = await loadCity(name); if (!alive) return;
          e.addCity(g); setGeo((s) => ({ ...s, [name]: g }));
          if (name === 'manhattan') setCands(e.candidates());
        } catch (err) { console.warn(`journey: ${name} unavailable`, err); }
      }
    })();
    return () => { alive = false; cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); c.removeEventListener('webglcontextlost', lost); e.dispose(); engine.current = null; };
  }, [p, still]);

  const m = geo.manhattan, ch = geo.chicago, cb = geo.cambridge;
  const L = (id: string) => (el: HTMLElement | null) => { labels.current[id] = el; };
  const km = (id: string) => cands.find((c) => c.id === id)?.km;
  const outro = useTransform(p, [0.955, 0.995], [0, 1]);
  const progress = useTransform(p, [0, 1], ['0%', '100%']);

  if (failed) return null;
  return (
    <section id="journey" ref={ref} className="relative bg-[#05070c] text-white" style={{ height: '1500vh' }} aria-label="A scroll-driven journey through real cities">
      <div className="sticky top-0 h-[100svh] overflow-hidden">
        <canvas ref={canvas} className="absolute inset-0 h-full w-full" aria-hidden />
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,rgba(5,7,12,.75)_100%)]" aria-hidden />

        {/* labels pinned to real places, moved every frame by the engine */}
        <div className="pointer-events-none absolute inset-0" aria-hidden>
          <div ref={L('rider')} className="jl" style={{ opacity: 0 }}>Rider waiting · Midtown</div>
          <div ref={L('pick')} className="jl yellow" style={{ opacity: 0 }}>ORCA’s pick · {km('pick')?.toFixed(1)} km by road</div>
          <div ref={L('near')} className="jl red" style={{ opacity: 0 }}>Nearest car · GPS silent 48 s</div>
          {cands.filter((c) => c.id.startsWith('c')).map((c) => <div key={c.id} ref={L(c.id)} className="jl" style={{ opacity: 0 }}>{c.km.toFixed(1)} km by road</div>)}
          {[0, 1, 2].map((i) => <div key={i} ref={L(`z${i}`)} className="jl violet" style={{ opacity: 0 }} />)}
        </div>

        <Title p={p} />

        <Beat p={p} at={[T.rise[0] + 0.005, T.rise[1]]} className="bottom-[10%] left-5 md:left-10">
          <p className="num wide text-[16vw] font-extrabold leading-[0.85] text-taxi md:text-[9vw]"><Counter p={p} at={[T.rise[0], T.rise[1] - 0.02]} to={m?.nBuildings ?? 44375} /></p>
          <p className="mt-3 max-w-md text-[17px] text-white/75 md:text-[20px]">real buildings rise from their official footprints ({m?.buildingSource ?? 'NYC Open Data'}), over {fmt(m?.nNodes ?? 4454)} real intersections from OpenStreetMap.</p>
        </Beat>

        <div className="absolute inset-0 flex flex-col justify-center gap-2 px-5 md:px-10">
          <Beat p={p} at={[T.traffic[0] + 0.004, T.traffic[1]]} from="left" className="left-5 top-[22%] md:left-10"><p className="wide text-[11vw] font-extrabold leading-none md:text-[5.6vw]">Thousands of cars.</p></Beat>
          <Beat p={p} at={[T.traffic[0] + 0.025, T.traffic[1]]} from="right" className="right-5 top-[42%] text-right md:right-10"><p className="wide text-[11vw] font-extrabold leading-none text-white/70 md:text-[5.6vw]">One question,</p></Beat>
          <Beat p={p} at={[T.traffic[0] + 0.045, T.traffic[1]]} from="left" className="left-5 top-[62%] md:left-10"><p className="wide text-[11vw] font-extrabold leading-none text-taxi md:text-[5.6vw]">which one goes?</p></Beat>
        </div>

        {[
          { at: [DECIDE.rider - 0.01, DECIDE.routes[0]] as const, k: 'A rider asks for a ride.', b: 'Midtown, a real intersection. The beam marks the pickup.' },
          { at: [DECIDE.routes[0], DECIDE.decide] as const, k: 'ORCA scores every nearby car.', b: 'The real fastest route through one-way streets, the chance the driver accepts, how fresh its GPS is, what demand is coming.' },
          { at: [DECIDE.decide, DECIDE.drive[0] + 0.005] as const, k: 'The nearest car is not the best one.', b: 'It went silent 48 seconds ago: an offer would go unheard. ORCA sends a car that will answer, and arrive.' },
          { at: [DECIDE.drive[0] + 0.005, T.decide[1]] as const, k: 'Dispatched.', b: 'The buildings around the rider sink away so you can watch it drive its real route.' },
        ].map((s) => (
          <Beat key={s.k} p={p} at={s.at} className="bottom-[8%] left-5 right-5 md:bottom-auto md:left-10 md:top-[30%] md:max-w-md">
            <div className="rounded-2xl bg-black/45 p-5 ring-1 ring-white/10 backdrop-blur-md">
              <p className="wide text-[28px] font-extrabold leading-tight md:text-[38px]">{s.k}</p>
              <p className="mt-2 text-[15px] leading-relaxed text-white/70 md:text-[17px]">{s.b}</p>
            </div>
          </Beat>
        ))}

        <Beat p={p} at={[T.batch[0] + 0.005, T.batch[1]]} className="inset-x-0 top-[26%] text-center">
          {cut != null && <p className="num wide text-[26vw] font-extrabold leading-[0.8] text-taxi md:text-[15vw]">{Math.round(cut * 100)}%</p>}
          <p className="mx-auto mt-4 max-w-2xl px-5 text-[18px] text-white/80 md:text-[24px]">shorter waits than nearest-car dispatch. Not one rider at a time: the whole city, matched together, every 3 seconds.</p>
        </Beat>

        <Beat p={p} at={[T.day[0] + 0.01, T.day[1]]} className="left-5 top-[16%] md:left-10">
          <p className="num wide text-[18vw] font-extrabold leading-none md:text-[10vw]"><span ref={clock}>00:00</span></p>
          <p className="mt-2 max-w-md text-[16px] text-white/75 md:text-[19px]">A real weekday in Manhattan. Each glass column is an official taxi zone: the taller and redder, the slower its streets at that hour, measured from {fmt(NYC?.traffic?.kept_trips ?? 0)} real trips.</p>
        </Beat>

        <CityCard p={p} at={T.chicago} name="Chicago" lines={<>
          <p className="wide text-[26px] font-extrabold md:text-[34px]">Same engine. Another city.</p>
          <p className="mt-2 text-[16px] text-white/75">{ch ? `${fmt(ch.nBuildings)} buildings and ${fmt(ch.nNodes)} intersections of downtown Chicago, with riders from the City of Chicago's trip records.` : 'Loading Chicago…'}</p>
        </>} />
        <CityCard p={p} at={T.cambridge} name="Cambridge" lines={<>
          <p className="wide text-[26px] font-extrabold md:text-[34px]">Any city on Earth.</p>
          <p className="mt-2 text-[16px] text-white/75">{cb ? `Built on demand from OpenStreetMap: ${fmt(cb.nBuildings)} buildings and ${fmt(cb.nNodes)} intersections of Cambridge, Massachusetts, with one command.` : 'Loading Cambridge…'}</p>
        </>} />

        <Beat p={p} at={[T.outro[0] + 0.01, 1.01]} className="inset-x-0 top-[30%] px-5 text-center">
          <p className="wide text-[12vw] font-extrabold leading-none md:text-[7vw]">Your turn.</p>
          <p className="mx-auto mt-4 max-w-xl text-[17px] text-white/75">Open the control room, pick a city, break it with outages and accidents, and click any rider to see why ORCA chose that car.</p>
          <div className="pointer-events-auto mt-8 flex flex-wrap justify-center gap-3">
            {[...CITIES.map((c) => [c.name, c.short]), ['cambridge', 'Cambridge']].map(([n, s], i) => (
              <a key={n} href={appUrl(n)} className={i === 0 ? 'btn-taxi text-[16px]' : 'btn text-[16px] text-white ring-1 ring-white/30 hover:ring-white'}>{s} <ArrowRight size={16} /></a>
            ))}
          </div>
        </Beat>

        {/* chapter rail */}
        <nav className="absolute right-4 top-1/2 hidden -translate-y-1/2 md:block" aria-label="Journey chapters">
          <ol className="space-y-2.5 text-right text-[12px] font-semibold uppercase tracking-[0.14em]">
            {CHAPTERS.map((c, i) => (
              <li key={c.key} className={`flex items-center justify-end gap-2 transition-all duration-300 ${i === chapter ? 'text-taxi' : 'text-white/35'}`}>
                <span className={i === chapter ? 'opacity-100' : 'opacity-0 md:opacity-60'}>{c.label}</span>
                <span className={`h-px transition-all duration-300 ${i === chapter ? 'w-8 bg-taxi' : 'w-3 bg-white/40'}`} />
              </li>
            ))}
          </ol>
        </nav>
        <motion.div className="absolute bottom-0 left-0 h-[3px] bg-taxi" style={{ width: progress }} aria-hidden />
        <Beat p={p} at={[-0.05, 0.012]} className="bottom-8 left-1/2 -translate-x-1/2 text-center text-[13px] text-white/60">
          <span className="flex flex-col items-center gap-2">Scroll<motion.span animate={still ? {} : { y: [0, 6, 0] }} transition={{ duration: 1.5, repeat: Infinity }}><ArrowDown size={18} /></motion.span></span>
        </Beat>
        {!m && <p className="absolute bottom-8 right-6 text-[12px] font-medium text-white/50">Loading real Manhattan…</p>}
        <motion.div className="pointer-events-none absolute inset-0 bg-paper" style={{ opacity: outro }} aria-hidden />
      </div>
    </section>
  );
}
