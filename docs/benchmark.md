# Benchmark

45 simulated minutes per run after a 5-minute warm-up, 1200 units per policy, seeds 1, 2. All policies see identical requests, traffic and incidents.
Demand replays real NYC TLC yellow-taxi trips (January 2015 weekdays, Manhattan) at the real hourly rate; citywide road speed follows the real median taxi speed for each hour.
The stress scenario injects a demand spike and a GPS outage at minute 20 and an accident at minute 40.

## Steady

| Policy | Mean wait | p95 wait | Served | Lost | Re-offers |
|---|---:|---:|---:|---:|---:|
| Nearest unit | 74s | 188s | 3891 | 0 | 1184 |
| ORCA, heuristic models | 40s | 100s | 3958 | 0 | 944 |
| ORCA, learned models | 39s | 96s | 3952 | 0 | 885 |

ORCA with learned models cuts mean wait by 47% against nearest-unit dispatch.

## Stress

| Policy | Mean wait | p95 wait | Served | Lost | Re-offers |
|---|---:|---:|---:|---:|---:|
| Nearest unit | 152s | 479s | 4297 | 14 | 4084 |
| ORCA, heuristic models | 88s | 329s | 4412 | 14 | 1216 |
| ORCA, learned models | 90s | 346s | 4412 | 10 | 1194 |

ORCA with learned models cuts mean wait by 40% against nearest-unit dispatch.
