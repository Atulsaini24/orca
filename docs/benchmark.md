# Benchmark

45 simulated minutes per run after a 5-minute warm-up, 1200 cars per dispatcher, 10 seeds (1-10). Both dispatchers see identical requests, traffic, incidents and drivers on every seed.
Demand replays real weekday trips (NYC Taxi & Limousine Commission: yellow taxi and Uber / Lyft (high-volume for-hire) trips, September 2026) at the real hourly rate; road speed follows the real median trip speed for each zone and hour.
The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.
ORCA's scoring weights are tuned by `orca.tune` on seeds 101-109, which the benchmark never uses.

Values are means over seeds with 95% bootstrap confidence intervals in brackets.

## Steady demand

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 136s (131-140) | 321s (304-337) | 2.0 | 410 | – |
| ORCA, heuristic models | 122s (121-123) | 277s (274-280) | 1.8 | 362 | 10% (7-13%) |
| ORCA, learned models | 109s (108-111) | 236s (232-242) | 1.4 | 385 | 19% (17-22%) |
| ORCA, learned, hand-set weights | 116s (113-119) | 257s (249-264) | 2.5 | 394 | 15% (13-16%) |
| ORCA, learned, no give-up guard | 109s (107-111) | 237s (232-242) | 1.5 | 386 | 20% (17-22%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.002; ORCA vs nearest p = 0.002; riders lost with vs without the give-up guard p = 1.000.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 186s (178-196) | 556s (524-591) | 13.4 | 1080 | – |
| ORCA, heuristic models | 163s (154-172) | 471s (412-526) | 7.0 | 448 | 12% (8-16%) |
| ORCA, learned models | 152s (146-159) | 469s (416-518) | 5.3 | 463 | 18% (14-21%) |
| ORCA, learned, hand-set weights | 160s (153-169) | 487s (439-537) | 9.1 | 467 | 13% (11-16%) |
| ORCA, learned, no give-up guard | 153s (146-160) | 467s (418-511) | 5.7 | 461 | 17% (14-21%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.004; ORCA vs nearest p = 0.002; riders lost with vs without the give-up guard p = 0.500.

## A full day

24 simulated hours from midnight on the real demand and traffic profile (seeds 1): mean wait 112s with ORCA vs 171s with nearest car (35% less); riders lost 57 vs 77.

| Hour | 00 | 03 | 06 | 09 | 12 | 15 | 18 | 21 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORCA | 75s | 63s | 86s | 128s | 134s | 128s | 114s | 93s |
| Nearest | 87s | 78s | 98s | 212s | 175s | 159s | 185s | 180s |

