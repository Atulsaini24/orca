# ORCA vs the industry baseline: Cambridge street network

Large ride-hailing platforms have publicly described replacing nearest-car dispatch with **batched matching**:
collect requests for a few seconds, then solve one assignment that minimises total predicted pickup time.
That is the baseline a dispatch system has to beat today, so this benchmark adds it.

The batched-ETA dispatcher uses ORCA's own batching window, candidate search, ETA model and exact
assignment solver, and nothing else. The gap between it and ORCA is therefore what ORCA's extra signals
add (driver acceptance odds, GPS freshness, rider wait and give-up deadline, forecast-driven repositioning),
not what batching adds. "ORCA, matching only" switches repositioning off, which splits that gap into
better matching and better positioning.

45 simulated minutes per run, 1200 cars per dispatcher, 10 seeds; every policy
sees identical requests, traffic, incidents and drivers on a seed. Means over seeds with 95% bootstrap
confidence intervals; p-values from paired Wilcoxon signed-rank tests over seeds.

## Steady demand

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 227s (211-245) | 574s (539-612) | 50.8 | 763 | – |
| Batched ETA matching (industry baseline) | 225s (210-242) | 572s (538-607) | 46.1 | 770 | 1% (-1 to 3%) |
| ORCA, matching only (no repositioning) | 225s (209-242) | 569s (533-610) | 42.9 | 688 | 1% (-0 to 2%) |
| ORCA, learned models | 142s (139-144) | 354s (341-367) | 19.8 | 634 | 37% (33-41%) |

**ORCA vs batched ETA:** mean wait 37% (33-40%) shorter, p95 wait 38% (35-41%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait 0% (-1 to 1%) shorter, p95 0% (-1 to 2%) (p = 0.695). Batched ETA vs nearest car: p = 0.625.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 289s (277-303) | 728s (710-746) | 149.7 | 2047 | – |
| Batched ETA matching (industry baseline) | 286s (274-300) | 720s (702-738) | 137.6 | 2029 | 1% (0-2%) |
| ORCA, matching only (no repositioning) | 287s (275-301) | 729s (707-752) | 145.8 | 872 | 1% (-0 to 2%) |
| ORCA, learned models | 224s (217-230) | 645s (619-670) | 81.0 | 793 | 23% (20-25%) |

**ORCA vs batched ETA:** mean wait 22% (19-24%) shorter, p95 wait 10% (7-13%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait -0% (-2 to 1%) shorter, p95 -1% (-3 to 0%) (p = 0.557). Batched ETA vs nearest car: p = 0.084.

Reproduce: `python -m orca.bench --city cambridge --vs-industry` (2 min on this machine).
