# Electric fleet

Manhattan, New York: 1,200 electric cars per dispatcher over a 12-hour shift from 07:00 on real demand, seeds 1-6, paired. Each car has a 75 kWh battery using 0.18 kWh per km and starts between 35% and 100% charged; below 20% it stops taking rides and charges to 80%; a ride is offered only to a car that can finish it with charge to spare. Charging stations are the city's 42 public stations in OpenStreetMap (9 fast, 64 fast plugs; 167 plugs in all).

| Policy | Mean wait | p95 wait | Riders lost | Queue at the charger | Car-hours charging | Sessions | Charge at the end |
|---|---:|---:|---:|---:|---:|---:|---:|
| Nearest car, drivers charge on their own | 194s (188-199) | 487s | 145 | 18.9 min | 274 | 210 | 48% |
| ORCA, drivers charge on their own | 127s (126-128) | 279s | 62 | 24.9 min | 360 | 215 | 46% |
| ORCA, ORCA plans charging | 126s (125-126) | 277s | 61 | 1.6 min | 206 | 290 | 50% |
| ORCA plans charging, with early top-ups | 125s (124-126) | 276s | 64 | 0.0 min | 291 | 612 | 58% |

Means over seeds, 95% bootstrap interval for the mean wait in brackets. "Car-hours charging" counts driving to a station, queueing and charging. No car ran out of charge in any run.

Left to themselves, drivers all head for the nearest fast charger and queue 24.9 minutes on average. ORCA sends each car to the station that returns it to service soonest, counting the cars already plugged in, queued and on their way, and the charging time at that station's power: the queue drops to 1.6 minutes and the fleet spends 43% fewer car-hours charging. Early top-ups keep batteries fuller but take cars off the road; on this shift they do not shorten waits, so they are off by default.

