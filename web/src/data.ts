// Every number on the home page comes from files the backend produces:
// the benchmark (python -m orca.bench), the model metrics (python -m orca.train)
// and the real-data profile (scripts/prepare_tlc.py). Nothing is typed in by hand.
import benchmark from '../../docs/benchmark.json';
import etaMetrics from '../../backend/artifacts/eta_metrics.json';
import demandMetrics from '../../backend/artifacts/demand_tlc_metrics.json';
import traffic from '../../backend/artifacts/nyc_traffic_profile.json';

export type Policy = 'nearest' | 'orca_heuristic' | 'orca_learned';
type Stats = { mean: number; p50: number; p95: number; served: number; lost: number; reoffers: number };
type Row = { scenario: string; seed: number } & Record<Policy, Stats>;
type Metric = { model: string; mae: number; p90_abs_err: number; r2: number };

const rows = (benchmark as { rows: Row[] }).rows;
export const benchConfig = (benchmark as { config: { minutes: number; seeds: number[]; fleet: number; demand?: string } }).config;

const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
export function agg(scenario: string, policy: Policy, key: keyof Stats): number {
  return avg(rows.filter((r) => r.scenario === scenario).map((r) => r[policy][key]));
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
export const cut = (scenario: string) => 1 - agg(scenario, 'orca_learned', 'mean') / agg(scenario, 'nearest', 'mean');

const pick = (rs: Metric[], needle: string) => rs.find((r) => r.model.toLowerCase().includes(needle));
const eta = etaMetrics as { results: Metric[]; n_train: number; n_test: number };
const dem = demandMetrics as { results: Metric[]; n_train: number; n_test: number };
export const models = {
  eta: { n: eta.n_train + eta.n_test, base: pick(eta.results, 'free-flow'), heur: pick(eta.results, 'heuristic'), ours: pick(eta.results, 'gradient') },
  demand: { n: dem.n_train + dem.n_test, base: pick(dem.results, 'ewma'), naive: pick(dem.results, 'last minute'), ours: pick(dem.results, 'gradient') },
};

export const realData = traffic as {
  source: string; raw_trips: number; kept_trips: number; days: number; zones: number; replay_trips: number;
  speed_mps: number[]; factor: number[]; trips_per_hour: number[];
};

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
export const fmtM = (n: number) => `${(n / 1e6).toFixed(1)}M`;
