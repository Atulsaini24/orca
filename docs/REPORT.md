# ORCA: dispatch that beats batched matching on real city demand

*Technical report. Every number below is reproducible with the command next to it; raw results are the
JSON files in this folder.*

## Summary

Ride-hailing dispatch decides which car takes each ride request. The industry moved years ago from
"send the nearest car" to **batched matching**: collect requests for a few seconds and solve one
assignment that minimises total predicted pickup time. ORCA keeps batched, exact assignment and adds
the signals a real dispatcher can observe but batched-ETA matching ignores:

1. **Driver acceptance**: a per-driver Beta-Bernoulli posterior, so offers go to drivers likely to say yes.
2. **GPS freshness**: cars whose GPS has gone quiet are not offered rides.
3. **Rider wait and give-up deadline**: riders who have waited long, or would give up before a car
   arrives, are served first.
4. **Zone supply**: a learned demand forecast per official zone prices taking the last idle car out of a
   zone about to run short, and idle cars are moved toward those zones ahead of demand.

On **real September 2026 demand** (2+ million NYC taxi, Uber and Lyft trips; Chicago's Uber, Lyft and
Via trips), **real street networks** (OpenStreetMap, one-way rules) and **real traffic** (median speed per
zone and hour), with 1,200 cars per dispatcher and 10 paired seeds:

* **Against batched ETA matching, ORCA cuts the mean wait by 11-13% and the p95 wait by 13-20%**, in both
  cities and both scenarios, winning on every one of the 10 seeds each time (p = 0.002).
* **Against nearest car, 15-19% shorter mean waits**, and under a demand spike, GPS outage and accident
  in Manhattan **riders lost fall from 12.3 to 6.8**.
* **Where the gain comes from:** an ablation shows almost all of the wait reduction over batched matching
  comes from **forecast-driven repositioning**; ORCA's extra matching signals alone change mean wait by
  only 0-3%, but they **halve wasted offers under stress** (467 vs 1,049 re-offers in Manhattan), because
  rides stop going to drivers who are silent or likely to decline.

## 1. Problem

A request arrives with a pickup point and a time. A fleet of cars, each idle, on a pickup or carrying a
rider, reports GPS every few seconds. The dispatcher offers the ride to one car; the driver may decline
(and the offer goes to another car) or may not answer at all (their GPS has gone silent). A rider who
waits more than 15 minutes gives up. The objective is short waits for every rider, with weight on the
slowest waits (p95) and riders lost, not only the mean.

## 2. System

| Part | What it does |
|---|---|
| City model | OpenStreetMap streets with one-way rules, official zones, official building footprints; any city from OpenStreetMap with one command (`python -m orca.newcity`) |
| Demand | Replays real trips at the real per-minute rate for each zone and hour |
| Traffic | Real median trip speed per zone and hour; incidents (accidents, GPS outages, demand spikes) on demand |
| Engine | Structure of arrays advanced by Numba-compiled kernels: 10,000 cars per dispatcher at 89x real time ([`scale.md`](scale.md)) |
| Matching | Every 3 s, k nearest fresh cars per request through a uniform grid, ETA model, acceptance and zone costs, then exact min-cost assignment (dense Hungarian, or sparse LAPJV for large batches: 5,000 requests in 225 ms) |
| Models | Gradient-boosted ETA on street-network features; Poisson demand forecaster per zone; online acceptance posterior. Evaluated on held-out data later in time than training |
| Serving | Starlette server streaming packed binary frames over Server-Sent Events; 400 concurrent viewers at 8.4 frames per second on one process ([`loadtest.md`](loadtest.md)); rate limits, operator token, security headers, Prometheus metrics, OpenTelemetry tracing, a clock-stall watchdog on `/api/health` |
| Interface | Hand-written WebGL2 control room (no 3D library) showing both dispatchers live on the real city; a Three.js scroll story on the home page |

## 3. Baselines

* **Nearest car**: each request, in arrival order, goes to the nearest idle car.
* **Batched ETA matching (industry baseline)**: ORCA's own batching window, candidate search, ETA model
  and exact solver, scoring a pair by predicted pickup time and nothing else; no acceptance model, no GPS
  freshness filter, no wait or deadline priority, no zone cost, no repositioning. Because it shares every
  other component, the gap between it and ORCA measures ORCA's extra signals, not batching or a better
  ETA model.
* **ORCA, matching only**: ORCA with repositioning switched off. It splits ORCA's gain over batched
  matching into better *matching* and better *positioning*.

All policies run on **identical** requests, traffic, incidents and drivers for a seed (one simulated
world, dispatchers side by side), so per-seed differences are paired.

## 4. Method

45 simulated minutes per run after a 5-minute warm-up; 10 seeds (1-10). Two scenarios: steady demand,
and stress (a demand spike and a GPS outage at minute 20, an accident at minute 40). Means over seeds
with 95% percentile-bootstrap intervals; paired Wilcoxon signed-rank tests over seeds (with 10 seeds the
smallest attainable p is 0.002, reached when one policy wins on every seed). ORCA's scoring weights were
tuned per city on seeds 101+, which no benchmark uses. Models are trained on earlier days and tested on
later ones.

## 5. Results: ORCA vs the industry baseline

Mean wait from request to pickup, seconds; 95% bootstrap interval over 10 seeds in brackets.

| City, scenario | Nearest car | Batched ETA (industry) | ORCA, matching only | **ORCA** | **ORCA vs batched ETA** |
|---|---:|---:|---:|---:|---:|
| Manhattan, steady | 134 (130-138) | 124 (120-129) | 124 (120-128) | **108** (106-111) | **−13%** (10-16%), p = 0.002 |
| Manhattan, spike + outage + accident | 184 (176-195) | 169 (161-180) | 167 (159-176) | **151** (144-157) | **−11%** (7-14%), p = 0.002 |
| Chicago, steady | 95 (92-100) | 90 (86-95) | 89 (84-96) | **80** (79-82) | **−11%** (8-14%), p = 0.002 |
| Chicago, spike + outage + accident | 118 (108-130) | 112 (101-123) | 108 (100-118) | **99** (92-107) | **−11%** (8-14%), p = 0.002 |
| *Cambridge, steady (synthetic demand)* | *227 (211-245)* | *225 (210-242)* | *225 (209-242)* | *142 (139-144)* | *−37% (33-40%), p = 0.002* |
| *Cambridge, spike + outage + accident (synthetic)* | *289 (277-303)* | *286 (274-300)* | *287 (275-301)* | *224 (217-230)* | *−22% (19-24%), p = 0.002* |

Cambridge (in italics) is a city built from OpenStreetMap alone: there are no public trip records, so
demand is synthetic, there are no trained models or tuned weights, and 1,200 cars are stretched thin
(over 2,000 re-offers and 140+ riders lost under stress with nearest car). It is not evidence about real
demand; it shows the same pattern out of sample, where positioning matters even more because supply is
short.

The slowest riders gain most. p95 wait, ORCA vs batched ETA: **−20%** (Manhattan, steady), **−13%**
(Manhattan, stress), **−15%** (Chicago, steady), **−13%** (Chicago, stress).

| Under stress | Batched ETA: riders lost | ORCA: riders lost | Batched ETA: re-offers | ORCA, matching only: re-offers | ORCA: re-offers |
|---|---:|---:|---:|---:|---:|
| Manhattan | 10.2 | **6.8** | 1,049 | 467 | **452** |
| Chicago | 0.3 | **0.0** | 713 | 314 | **302** |

**Reading the ablation.**

1. *Batching helps, modestly.* Batched ETA matching beats nearest car by 5-8% (p ≤ 0.004), consistent
   with why the industry adopted it.
2. *ORCA's matching signals make dispatch cleaner, not much faster.* With repositioning off, ORCA's mean
   wait is within 0-3% of batched ETA (significant only in Chicago under stress, p = 0.02), yet re-offers
   fall by 55-56% under stress. Each avoided re-offer is a driver not pinged for a ride they would
   decline or never see, and a rider not left waiting on a silent car.
3. *Positioning is where the wait goes.* Adding forecast-driven repositioning takes ORCA from parity to
   11-13% shorter mean waits and 13-20% shorter p95 waits than batched ETA. Matching can only choose
   among cars that are already near; the forecast decides which cars are near.

Full tables: [`industry_manhattan.md`](industry_manhattan.md), [`industry_chicago.md`](industry_chicago.md),
[`industry_cambridge.md`](industry_cambridge.md). Reproduce: `python -m orca.bench --city <city> --vs-industry`.

## 6. Further results

| | Result | Source |
|---|---|---|
| Learned vs heuristic models | Learned ETA and demand models beat the fallbacks on mean wait in both cities and both scenarios (p = 0.002) | [`benchmark_manhattan.md`](benchmark_manhattan.md) |
| A full 24-hour day | Mean wait 112 s vs 171 s with nearest car in Manhattan (−35%); Chicago 79 s vs 116 s, riders lost 1 vs 81 | [`benchmark_chicago.md`](benchmark_chicago.md) |
| Shared rides | Half the riders accepting a shared ride, ORCA keeps the 1,200-car mean wait with about 1,083 cars (10% fewer, 95% interval 5-13%) | [`pooling_manhattan.md`](pooling_manhattan.md) |
| Electric fleet | On the city's 42 real public stations, charger queue 25 → 1.6 min, car-hours charging −43%, no car stranded | [`ev_manhattan.md`](ev_manhattan.md) |
| Model accuracy (held out, later in time) | Pickup ETA MAE 42.6 → 34.7 s (Manhattan); rides per zone in the next 5 min 1.25 → 0.75 | README |
| Calibration | Median routed trip matches the median real trip (Manhattan 15.2 min, Chicago 11.6 min); per-trip correlation 0.73 / 0.72 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| A negative result | A value network trained by fitted TD learning recovers 39-97% of the rule-based rebalancer's gain but does not beat it; the rules stay the default | [`rl_manhattan.md`](rl_manhattan.md) |

## 7. Limitations

* **Simulation, not a fleet.** Drivers, acceptance and GPS loss are modelled, not observed. Results show
  what the decisions do in a calibrated world; a pilot on real dispatch logs is the test that matters.
* **Scale of the replay.** 1,200 cars serve a sampled share of the city's demand; real platforms run
  thousands of drivers on all of it. Simulated waits (about 1:50) are the same order as the real TLC
  request-to-arrival time (3:59 average) but not a like-for-like comparison.
* **The baseline is a faithful reconstruction, not a platform's code.** Platforms describe batched
  ETA matching publicly; their production systems add signals of their own (some overlap with ORCA's).
* **Cities without trip records** (Cambridge, built from OpenStreetMap) run synthetic demand with no
  trained models or tuned weights. ORCA's large edge there (−22 to −37% vs batched ETA) comes from
  repositioning an under-supplied fleet and should not be read as a real-demand result.
* **One machine.** Each city runs in its own supervised process behind one port (`orca.router`), but a
  city's state lives in memory, so one city cannot yet be split across machines; the path there is in
  [`ARCHITECTURE.md`](ARCHITECTURE.md#scaling-this-out).

## 8. Reproduce

```bash
make install && make data          # dependencies, real trips for both cities
make industry                                           # this report's section 5, every city (about 10 min)
python -m orca.bench --city manhattan                   # full benchmark with ablations and a 24-hour day
cd backend && python -m pytest -q                       # 100+ tests, including browser end-to-end tests
```
