# Electric fleet

Chicago, downtown core: 1,200 electric cars per dispatcher over a 12-hour shift from 07:00 on real demand, seeds 1-6, paired. Each car has a 75 kWh battery using 0.18 kWh per km and starts between 35% and 100% charged; below 20% it stops taking rides and charges to 80%; a ride is offered only to a car that can finish it with charge to spare. Charging stations are the city's 26 public stations in OpenStreetMap (15 fast, 87 fast plugs; 124 plugs in all).

| Policy | Mean wait | p95 wait | Riders lost | Queue at the charger | Car-hours charging | Sessions | Charge at the end |
|---|---:|---:|---:|---:|---:|---:|---:|
| Nearest car, drivers charge on their own | 121s (119-123) | 358s | 61 | 1.2 min | 208 | 373 | 50% |
| ORCA, drivers charge on their own | 85s (84-86) | 207s | 1 | 1.4 min | 222 | 389 | 50% |
| ORCA, ORCA plans charging | 86s (85-87) | 207s | 1 | 5.3 min | 169 | 386 | 50% |
| ORCA plans charging, with early top-ups | 85s (84-86) | 205s | 1 | 0.0 min | 292 | 769 | 57% |

Means over seeds, 95% bootstrap interval for the mean wait in brackets. "Car-hours charging" counts driving to a station, queueing and charging. No car ran out of charge in any run.

Left to themselves, drivers all head for the nearest fast charger and queue 1.4 minutes on average. ORCA sends each car to the station that returns it to service soonest, counting the cars already plugged in, queued and on their way, and the charging time at that station's power: with fast plugs plentiful it accepts a longer queue (5.3 minutes) at faster or closer stations and the fleet spends 24% fewer car-hours charging. Early top-ups keep batteries fuller but take cars off the road; on this shift they do not shorten waits, so they are off by default.

