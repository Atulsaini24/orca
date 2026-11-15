# Benchmark

45 simulated minutes per run after a 5-minute warm-up, 1200 cars per dispatcher, 10 seeds (1-10). Both dispatchers see identical requests, traffic, incidents and drivers on every seed.
Demand replays real NYC TLC yellow-taxi trips (January 2015 Manhattan weekdays) at the real hourly rate; citywide road speed follows the real median taxi speed for each hour.
The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.
ORCA's scoring weights are tuned by `orca.tune` on seeds 101-109, which the benchmark never uses.

Values are means over seeds with 95% bootstrap confidence intervals in brackets.

## Steady demand

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 68s (61-74) | 176s (158-195) | 0.0 | 1164 | – |
| ORCA, heuristic models | 35s (33-37) | 94s (88-100) | 0.0 | 895 | 47% (44-50%) |
| ORCA, learned models | 33s (31-36) | 88s (81-95) | 0.0 | 857 | 50% (48-52%) |
| ORCA, learned, hand-set weights | 37s (35-39) | 92s (85-98) | 0.0 | 919 | 45% (41-47%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.002; ORCA vs nearest p = 0.002.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 136s (116-158) | 428s (369-489) | 9.0 | 3628 | – |
| ORCA, heuristic models | 78s (65-91) | 293s (230-355) | 7.0 | 1205 | 42% (33-49%) |
| ORCA, learned models | 74s (61-87) | 276s (215-337) | 5.8 | 1196 | 45% (37-52%) |
| ORCA, learned, hand-set weights | 78s (65-92) | 288s (224-354) | 6.1 | 1201 | 42% (35-49%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.027; ORCA vs nearest p = 0.002.

## A full day

24 simulated hours from midnight on the real demand and traffic profile (seeds 1): mean wait 25s with ORCA vs 59s with nearest car (58% less); riders lost 0 vs 1.

| Hour | 00 | 03 | 06 | 09 | 12 | 15 | 18 | 21 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORCA | 22s | 27s | 23s | 26s | 26s | 23s | 25s | 25s |
| Nearest | 37s | 25s | 33s | 56s | 42s | 34s | 80s | 111s |
