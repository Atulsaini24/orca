import { motion, useReducedMotion } from 'framer-motion';

// A stylised slice of the street grid: free cars, a request ring, and ORCA's chosen car driving to it.
const N = 9, S = 52, W = (N - 1) * S;
const routes: { path: [number, number][]; dur: number; delay: number; ring: [number, number] }[] = [
  { path: [[0, 2], [3, 2], [3, 5], [5, 5]], dur: 6, delay: 0, ring: [5, 5] },
  { path: [[8, 7], [6, 7], [6, 3], [7, 3]], dur: 7, delay: 1.5, ring: [7, 3] },
  { path: [[2, 8], [2, 6], [1, 6], [1, 1]], dur: 6.5, delay: 3, ring: [1, 1] },
];
const idle: [number, number][] = [[4, 0.4], [7.3, 1], [0.5, 4], [5, 8], [8, 5.2], [3.6, 7], [6, 0], [2, 3.4]];
const P = ([x, y]: [number, number]) => [x * S, y * S] as const;

export default function HeroMap() {
  const still = useReducedMotion();
  return (
    <svg viewBox={`-30 -30 ${W + 60} ${W + 60}`} className="h-full w-full" role="img"
      aria-label="Illustration: ORCA routes a car along the street grid to a waiting rider">
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6" /></filter>
      </defs>
      <rect x={-30} y={-30} width={W + 60} height={W + 60} rx={28} fill="#E7EBEF" />
      {Array.from({ length: N }, (_, i) => (
        <g key={i} stroke={i % 4 === 0 ? '#FFFFFF' : '#F6F7F8'} strokeWidth={i % 4 === 0 ? 9 : 5} strokeLinecap="round">
          <line x1={i * S} y1={0} x2={i * S} y2={W} />
          <line x1={0} y1={i * S} x2={W} y2={i * S} />
        </g>
      ))}
      {Array.from({ length: (N - 1) * (N - 1) }, (_, k) => {
        const x = k % (N - 1), y = Math.floor(k / (N - 1)), h = 10 + ((x * 7 + y * 13) % 5) * 5;
        return <rect key={k} x={x * S + 9} y={y * S + 9} width={S - 18} height={S - 18} rx={5} fill="#D5DAE0" opacity={0.35 + h / 60} />;
      })}
      {idle.map(([x, y], i) => (
        <rect key={i} x={x * S - 6} y={y * S - 6} width={12} height={12} rx={3} fill="#737B86" stroke="#fff" strokeWidth={2} />
      ))}
      {routes.map((r, i) => {
        const pts = r.path.map(P);
        const d = 'M' + pts.map((p) => p.join(' ')).join(' L');
        const [rx, ry] = P(r.ring);
        const times = pts.map((_, k) => k / (pts.length - 1));
        return (
          <g key={i}>
            <motion.path d={d} fill="none" stroke="#FFC20A" strokeWidth={5} strokeLinecap="round" strokeLinejoin="round"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={still ? { pathLength: 1, opacity: 0.9 } : { pathLength: [0, 1, 1], opacity: [0.9, 0.9, 0] }}
              transition={{ duration: r.dur, delay: r.delay, repeat: still ? 0 : Infinity, repeatDelay: 1.2, times: [0, 0.75, 1] }} />
            <circle cx={rx} cy={ry} r={16} fill="#FFC20A" opacity={0.35} filter="url(#glow)" />
            <motion.circle cx={rx} cy={ry} r={11} fill="none" stroke="#0B0C0E" strokeWidth={3}
              animate={still ? {} : { scale: [1, 1.35, 1] }} transition={{ duration: 1.6, repeat: Infinity }}
              style={{ transformOrigin: `${rx}px ${ry}px` }} />
            <motion.rect width={16} height={16} rx={4} x={-8} y={-8} fill="#FFC20A" stroke="#0B0C0E" strokeWidth={2.5}
              initial={{ x: pts[0][0] - 8, y: pts[0][1] - 8 }}
              animate={still ? { x: pts[pts.length - 1][0] - 8, y: pts[pts.length - 1][1] - 8 }
                : { x: pts.map((p) => p[0] - 8), y: pts.map((p) => p[1] - 8) }}
              transition={{ duration: r.dur * 0.75, delay: r.delay, times, ease: 'easeInOut', repeat: still ? 0 : Infinity,
                repeatDelay: r.dur * 0.25 + 1.2 }} />
          </g>
        );
      })}
    </svg>
  );
}
