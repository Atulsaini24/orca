// Every number on the home page comes from files the backend produces, per city:
// the benchmark (python -m orca.bench), model metrics (python -m orca.train), the real-data profile
// (scripts/prepare_trips.py), the city build (scripts/build_*.py) and the speed calibration.
// Files are picked up with import.meta.glob, so a city that has not been built simply does not appear.
const arts = import.meta.glob([                     // only the files this page reads, so nothing else is bundled
  '../../backend/artifacts/{eta_metrics,demand_tlc_metrics,eta_real_metrics,traffic,city,speed_calibration,real_waits}_*.json',
], { eager: true, import: 'default' }) as Record<string, unknown>;
const docs = import.meta.glob('../../docs/benchmark_*.json', { eager: true, import: 'default' }) as Record<string, unknown>;
const art = <T,>(name: string) => arts[`../../backend/artifacts/${name}.json`] as T | undefined;

export type Policy = 'nearest' | 'orca_heuristic' | 'orca_learned' | 'orca_untuned' | 'orca_no_guard';
type Stats = { mean: number; p50: number; p95: number; served: number; lost: number; reoffers: number };
type Row = { scenario: string; seed: number } & Partial<Record<Policy, Stats>>;
type CI = { value: number; ci: [number, number] };
type PolicySummary = Record<'mean' | 'p50' | 'p95' | 'served' | 'lost' | 'reoffers', CI> & { reduction_vs_nearest?: CI };
type Tests = { learned_vs_heuristic: number | null; tuned_vs_untuned: number | null; learned_vs_nearest: number | null; guard_lost?: number | null };
type FullDay = { seeds: number[]; orca: (number | null)[]; nearest: (number | null)[];
  totals: Record<'orca' | 'nearest', { mean: number; served: number; lost: number }> };
type Bench = {
  rows: Row[];
  config: { minutes: number; seeds: number[]; fleet: number; demand?: string; tuned_weights?: boolean; city?: string };
  summary?: Record<string, Partial<Record<Policy, PolicySummary>>>;
  tests?: Record<string, Tests>;
  full_day?: FullDay | null;
};
type Metric = { model: string; mae: number; p90_abs_err?: number; r2?: number; median_abs_err?: number; mape?: number };

const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);

export function benchView(b: Bench) {
  const agg = (scenario: string, policy: Policy, key: keyof Stats) =>
    avg(b.rows.filter((r) => r.scenario === scenario && r[policy]).map((r) => (r[policy] as Stats)[key]));
  const ciOf = (scenario: string, policy: Policy, key: keyof PolicySummary = 'mean'): CI | undefined => b.summary?.[scenario]?.[policy]?.[key];
  const has = (policy: Policy) => b.rows.some((r) => r[policy]);
  return {
    config: b.config, tests: b.tests, fullDay: b.full_day ?? null, agg, ciOf, has,
    cut: (s: string) => ciOf(s, 'orca_learned', 'reduction_vs_nearest')?.value ?? 1 - agg(s, 'orca_learned', 'mean') / agg(s, 'nearest', 'mean'),
    cutCI: (s: string) => ciOf(s, 'orca_learned', 'reduction_vs_nearest')?.ci,
  };
}
export type BenchView = ReturnType<typeof benchView>;

const pick = (rs: Metric[] | undefined, needle: string) => rs?.find((r) => r.model.toLowerCase().includes(needle));

export type CityData = ReturnType<typeof cityData>;
function cityData(name: string, short: string, long: string, dataLabel: string, credit: string) {
  const bench = docs[`../../docs/benchmark_${name}.json`] as Bench | undefined;
  const eta = art<{ results: Metric[]; n_train: number; n_test: number }>(`eta_metrics_${name}`);
  const dem = art<{ results: Metric[]; n_train: number; n_test: number }>(`demand_tlc_metrics_${name}`);
  const er = art<{ results: Metric[]; n_train: number; n_test: number; train_days: string[]; test_days: string[] }>(`eta_real_metrics_${name}`);
  return {
    name, short, long, dataLabel, credit,
    bench: bench ? benchView(bench) : null,
    eta: eta ? { n: eta.n_train + eta.n_test, heur: pick(eta.results, 'heuristic'), ours: pick(eta.results, 'gradient') } : null,
    demand: dem ? { n: dem.n_train + dem.n_test, base: pick(dem.results, 'ewma'), naive: pick(dem.results, 'last minute'), ours: pick(dem.results, 'gradient') } : null,
    realEta: er ? { ...er, ours: er.results[er.results.length - 1], baselines: er.results.slice(0, -1) } : null,
    traffic: art<{ source: string; raw_trips: number; kept_trips: number; days: number; zones: number; speed_mps: number[];
      trips_per_hour: number[]; services?: Record<string, number>; zone_hour_factor?: number[][] }>(`traffic_${name}`) ?? null,
    facts: art<{ intersections: number; segments: number; zones: number; street_km: number; one_way_share: number; width_km: number;
      length_km: number; buildings?: number; tallest_m?: number; buildings_over_150m?: number }>(`city_${name}`) ?? null,
    calibration: art<{ speed_scale: number; trips: number; median_real_s: number; median_sim_s_after: number; median_abs_err_s_after: number;
      corr: number }>(`speed_calibration_${name}`) ?? null,
    waits: art<{ source: string; trips: number; mean_on_scene_s: number; on_scene_p50: number; on_scene_p90: number; mean_to_board_s: number;
      by_service?: Record<string, number> }>(`real_waits_${name}`) ?? null,
  };
}

export const CITIES = [
  cityData('manhattan', 'Manhattan', 'Manhattan, New York', 'NYC Taxi & Limousine Commission',
    'Trips: NYC TLC. Buildings: NYC Open Data.'),
  cityData('chicago', 'Chicago', 'Chicago, downtown core', 'City of Chicago (TNP)',
    'Trips and buildings: City of Chicago Data Portal.'),
].filter((c) => c.facts);

export const policies: { key: Policy; label: string; tone: string }[] = [
  { key: 'nearest', label: 'Nearest car', tone: '#8B929B' },
  { key: 'orca_heuristic', label: 'ORCA, heuristic models', tone: '#C79200' },
  { key: 'orca_learned', label: 'ORCA, learned models', tone: '#FFC20A' },
];
export const scenarios = [
  { key: 'steady', label: 'Steady weekday demand' },
  { key: 'stress', label: 'Demand spike + GPS outage + accident' },
] as const;

// Beyond dispatch: one headline per study, from the benchmark files in docs/ (absent until that study is run)
const studies = import.meta.glob(['../../docs/{pooling,ev,rl}_manhattan.json', '../../docs/{scale,loadtest}.json'],
  { eager: true, import: 'default' }) as Record<string, unknown>;
const study = <T,>(name: string) => studies[`../../docs/${name}.json`] as T | undefined;
type Ci = { value: number; ci: [number, number] };
export const BEYOND = {
  pooling: study<{ headline: { ref_wait: number; fleet_needed: number; cars_saved: number; cars_saved_ci: [number, number] } | null;
    config: { demand_for: number; pool: { POOL_OPT_IN: number } } }>('pooling_manhattan'),
  ev: study<{ config: { stations: number; hours: number }; summary: Record<string, { queue_wait: Ci; charge_hours: Ci; stranded: Ci }> }>('ev_manhattan'),
  rl: study<{ scenarios: Record<string, { rules: { mean: number }; learned: { mean: number }; off: { mean: number } }> }>('rl_manhattan'),
  scale: study<{ fleets: { fleet: number; ms_per_sim_s: number; realtime: number }[]; previous: Record<string, [number, number]> }>('scale'),
  load: study<{ rows: { viewers: number; fps_mean: number }[] }>('loadtest'),
};

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
export const fmtM = (n: number) => `${(n / 1e6).toFixed(1)}M`;
export const mmss = (s: number) => { const r = Math.max(0, Math.round(s)); return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, '0')}`; };
