# ORCA vs the industry baseline: Manhattan street network

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
| Nearest car | 134s (130-138) | 313s (298-328) | 1.6 | 408 | – |
| Batched ETA matching (industry baseline) | 124s (120-129) | 297s (280-314) | 2.3 | 412 | 7% (6-8%) |
| ORCA, matching only (no repositioning) | 124s (120-128) | 289s (272-307) | 2.0 | 390 | 7% (7-8%) |
| ORCA, learned models | 108s (106-111) | 236s (229-246) | 1.7 | 381 | 19% (17-22%) |

**ORCA vs batched ETA:** mean wait 13% (10-16%) shorter, p95 wait 20% (15-25%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait 0% (-1 to 1%) shorter, p95 3% (1-5%) (p = 0.695). Batched ETA vs nearest car: p = 0.002.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 184s (176-195) | 549s (511-594) | 12.3 | 1082 | – |
| Batched ETA matching (industry baseline) | 169s (161-180) | 516s (473-558) | 10.2 | 1049 | 8% (7-9%) |
| ORCA, matching only (no repositioning) | 167s (159-176) | 503s (465-543) | 11.4 | 467 | 9% (8-11%) |
| ORCA, learned models | 151s (144-157) | 449s (401-493) | 6.8 | 452 | 18% (14-22%) |

**ORCA vs batched ETA:** mean wait 11% (7-14%) shorter, p95 wait 13% (9-18%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait 1% (-0 to 3%) shorter, p95 2% (-0 to 5%) (p = 0.275). Batched ETA vs nearest car: p = 0.002.

Reproduce: `python -m orca.bench --city manhattan --vs-industry` (3 min on this machine).
