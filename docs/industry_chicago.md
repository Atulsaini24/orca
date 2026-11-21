# ORCA vs the industry baseline: Chicago street network

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
| Nearest car | 95s (92-100) | 236s (223-257) | 0.0 | 293 | – |
| Batched ETA matching (industry baseline) | 90s (86-95) | 216s (201-237) | 0.0 | 296 | 5% (4-7%) |
| ORCA, matching only (no repositioning) | 89s (84-96) | 213s (196-239) | 0.0 | 272 | 7% (4-10%) |
| ORCA, learned models | 80s (79-82) | 181s (174-188) | 0.0 | 270 | 15% (14-18%) |

**ORCA vs batched ETA:** mean wait 11% (8-14%) shorter, p95 wait 15% (10-20%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait 2% (-0 to 3%) shorter, p95 2% (-2 to 4%) (p = 0.193). Batched ETA vs nearest car: p = 0.004.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 118s (108-130) | 330s (284-381) | 0.3 | 751 | – |
| Batched ETA matching (industry baseline) | 112s (101-123) | 307s (258-365) | 0.3 | 713 | 6% (4-8%) |
| ORCA, matching only (no repositioning) | 108s (100-118) | 300s (255-354) | 0.1 | 314 | 8% (7-10%) |
| ORCA, learned models | 99s (92-107) | 266s (224-317) | 0.0 | 302 | 16% (14-18%) |

**ORCA vs batched ETA:** mean wait 11% (8-14%) shorter, p95 wait 13% (8-18%) shorter (p = 0.002). With repositioning switched off, ORCA's matching alone: mean wait 3% (1-5%) shorter, p95 2% (-1 to 4%) (p = 0.020). Batched ETA vs nearest car: p = 0.002.

Reproduce: `python -m orca.bench --city chicago --vs-industry` (2 min on this machine).
