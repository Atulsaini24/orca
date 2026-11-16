# Benchmark

45 simulated minutes per run after a 5-minute warm-up, 1200 cars per dispatcher, 10 seeds (1-10). Both dispatchers see identical requests, traffic, incidents and drivers on every seed.
Demand replays real weekday trips (City of Chicago: Transportation Network Providers (Uber, Lyft, Via) trips, September 2026) at the real hourly rate; road speed follows the real median trip speed for each zone and hour.
The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.
ORCA's scoring weights are tuned by `orca.tune` on seeds 101-109, which the benchmark never uses.

Values are means over seeds with 95% bootstrap confidence intervals in brackets.

## Steady demand

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 97s (92-103) | 242s (226-265) | 0.0 | 288 | – |
| ORCA, heuristic models | 88s (86-90) | 211s (202-221) | 0.0 | 281 | 9% (5-13%) |
| ORCA, learned models | 81s (80-83) | 180s (175-187) | 0.0 | 272 | 15% (12-19%) |
| ORCA, learned, hand-set weights | 84s (82-86) | 187s (181-195) | 0.0 | 281 | 13% (10-17%) |
| ORCA, learned, no give-up guard | 81s (79-83) | 180s (174-187) | 0.0 | 269 | 16% (13-20%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.064; ORCA vs nearest p = 0.002.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 118s (109-129) | 332s (289-380) | 0.3 | 729 | – |
| ORCA, heuristic models | 106s (98-116) | 297s (248-358) | 0.0 | 311 | 10% (6-14%) |
| ORCA, learned models | 99s (92-108) | 273s (229-327) | 0.0 | 308 | 16% (13-19%) |
| ORCA, learned, hand-set weights | 103s (96-112) | 277s (236-329) | 0.0 | 339 | 13% (10-15%) |
| ORCA, learned, no give-up guard | 99s (92-108) | 272s (229-326) | 0.0 | 308 | 16% (13-19%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.010; ORCA vs nearest p = 0.002.

## A full day

24 simulated hours from midnight on the real demand and traffic profile (seeds 1): mean wait 79s with ORCA vs 116s with nearest car (32% less); riders lost 1 vs 81.

| Hour | 00 | 03 | 06 | 09 | 12 | 15 | 18 | 21 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORCA | 53s | 52s | 62s | 91s | 72s | 79s | 92s | 74s |
| Nearest | 61s | 45s | 73s | 185s | 96s | 89s | 145s | 113s |

