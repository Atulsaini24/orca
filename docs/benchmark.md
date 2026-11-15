# Benchmark

45 simulated minutes per run after a 5-minute warm-up, 1200 cars per dispatcher, 10 seeds (1-10). Both dispatchers see identical requests, traffic, incidents and drivers on every seed.
Demand replays real NYC TLC yellow-taxi trips (January 2015 Manhattan weekdays) at the real hourly rate; citywide road speed follows the real median taxi speed for each hour.
The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.
ORCA's scoring weights are tuned by `orca.tune` on seeds 101-109, which the benchmark never uses.

Values are means over seeds with 95% bootstrap confidence intervals in brackets.

## Steady demand

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 286s (271-301) | 669s (631-705) | 49.5 | 905 | – |
| ORCA, heuristic models | 162s (158-167) | 379s (360-398) | 4.6 | 626 | 43% (41-45%) |
| ORCA, learned models | 150s (145-154) | 357s (338-375) | 3.2 | 627 | 47% (45-49%) |
| ORCA, learned, hand-set weights | 150s (146-155) | 346s (330-364) | 4.4 | 624 | 47% (46-49%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 1.000; ORCA vs nearest p = 0.002.

## Spike, GPS outage and accident

| Policy | Mean wait | p95 wait | Riders lost | Re-offers | Wait cut vs nearest |
|---|---:|---:|---:|---:|---:|
| Nearest car | 319s (308-331) | 767s (755-780) | 279.3 | 2722 | – |
| ORCA, heuristic models | 227s (213-243) | 620s (582-657) | 88.4 | 871 | 29% (24-33%) |
| ORCA, learned models | 206s (195-218) | 557s (527-589) | 110.4 | 873 | 35% (32-39%) |
| ORCA, learned, hand-set weights | 215s (204-228) | 578s (551-606) | 110.5 | 879 | 33% (29-36%) |

Paired Wilcoxon signed-rank tests over seeds: learned vs heuristic models p = 0.002; tuned vs hand-set weights p = 0.010; ORCA vs nearest p = 0.002.

## A full day

24 simulated hours from midnight on the real demand and traffic profile (seeds 1): mean wait 107s with ORCA vs 210s with nearest car (49% less); riders lost 77 vs 824.

| Hour | 00 | 03 | 06 | 09 | 12 | 15 | 18 | 21 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ORCA | 72s | 79s | 95s | 132s | 115s | 105s | 117s | 88s |
| Nearest | 85s | 89s | 150s | 284s | 158s | 154s | 352s | 252s |
