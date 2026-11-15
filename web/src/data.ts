// Every number on the home page comes from files the backend produces:
// the benchmark (python -m orca.bench), the model metrics (python -m orca.train)
// and the real-data profile (scripts/prepare_tlc.py). Nothing is typed in by hand.
import benchmark from '../../docs/benchmark.json';
import etaMetrics from '../../backend/artifacts/eta_metrics.json';
import demandMetrics from '../../backend/artifacts/demand_tlc_metrics.json';
import traffic from '../../backend/artifacts/nyc_traffic_profile.json';
import etaReal from '../../backend/artifacts/eta_real_metrics.json';

export type Policy = 'nearest' | 'orca_heuristic' | 'orca_learned' | 'orca_untuned';
type Stats = { mean: number; p50: number; p95: number; served: number; lost: number; reoffers: number };
type Row = { scenario: string; seed: number } & Partial<Record<Policy, Stats>>;
type CI = { value: number; ci: [number, number] };
type PolicySummary = Record<'mean' | 'p50' | 'p95' | 'served' | 'lost' | 'reoffers', CI> & { reduction_vs_nearest?: CI };
type Bench = {
  rows: Row[];
  config: { minutes: number; seeds: number[]; fleet: number; demand?: string; tuned_weights?: boolean };
  summary?: Record<string, Partial<Record<Policy, PolicySummary>>>;
  tests?: Record<string, { learned_vs_heuristic: number | null; tuned_vs_untuned: number | null; learned_vs_nearest: number | null }>;
  full_day?: { seeds: number[]; orca: (number | null)[]; nearest: (number | null)[];
    totals: Record<'orca' | 'nearest', { mean: number; served: number; lost: number }> } | null;
};
const bench = benchmark as unknown as Bench;
type Metric = { model: string; mae: number; p90_abs_err: number; r2: number };

const rows = bench.rows;
export const benchConfig = bench.config;
export const benchTests = bench.tests;
export const fullDay = bench.full_day ?? null;
/** 95% bootstrap interval over seeds for a policy's metric, when the benchmark recorded one. */
export const ciOf = (scenario: string, policy: Policy, key: keyof PolicySummary = 'mean'): CI | undefined =>
  bench.summary?.[scenario]?.[policy]?.[key];

const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
export function agg(scenario: string, policy: Policy, key: keyof Stats): number {
  return avg(rows.filter((r) => r.scenario === scenario && r[policy]).map((r) => (r[policy] as Stats)[key]));
}
export const scenarios = [
  { key: 'steady', label: 'Steady weekday demand' },
  { key: 'stress', label: 'Demand spike + GPS outage + accident' },
] as const;
export const policies: { key: Policy; label: string; tone: string }[] = [
  { key: 'nearest', label: 'Nearest car', tone: '#8B929B' },
  { key: 'orca_heuristic', label: 'ORCA, heuristic models', tone: '#C79200' },
  { key: 'orca_learned', label: 'ORCA, learned models', tone: '#FFC20A' },
];
export const cut = (scenario: string) => ciOf(scenario, 'orca_learned', 'reduction_vs_nearest')?.value
  ?? 1 - agg(scenario, 'orca_learned', 'mean') / agg(scenario, 'nearest', 'mean');
export const cutCI = (scenario: string) => ciOf(scenario, 'orca_learned', 'reduction_vs_nearest')?.ci;

const pick = (rs: Metric[], needle: string) => rs.find((r) => r.model.toLowerCase().includes(needle));
const eta = etaMetrics as { results: Metric[]; n_train: number; n_test: number };
const dem = demandMetrics as { results: Metric[]; n_train: number; n_test: number };
export const models = {
  eta: { n: eta.n_train + eta.n_test, base: pick(eta.results, 'free-flow'), heur: pick(eta.results, 'heuristic'), ours: pick(eta.results, 'gradient') },
  demand: { n: dem.n_train + dem.n_test, base: pick(dem.results, 'ewma'), naive: pick(dem.results, 'last minute'), ours: pick(dem.results, 'gradient') },
};

type RealMetric = { model: string; mae: number; median_abs_err: number; p90_abs_err: number; mape: number };
const er = etaReal as { results: RealMetric[]; n_train: number; n_test: number; train_days: string[]; test_days: string[] };
export const realEta = { ...er, ours: er.results[er.results.length - 1], baselines: er.results.slice(0, -1) };

export const realData = traffic as {
  source: string; raw_trips: number; kept_trips: number; days: number; zones: number; replay_trips: number;
  speed_mps: number[]; factor: number[]; trips_per_hour: number[];
};

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
export const fmtM = (n: number) => `${(n / 1e6).toFixed(1)}M`;
