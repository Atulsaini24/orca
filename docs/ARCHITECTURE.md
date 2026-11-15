# Architecture

```
                         ┌──────────────────────────── one process today ─────────────────────────────┐
 NYC TLC trip replay ────┼─▶ World ──requests──▶ NearestDispatcher ──┐                                  │
 (or synthetic demand)   │     │ real hourly traffic, incidents       ├─▶ Engine ─▶ Starlette ─▶ middleware ─┼─▶ browser
                         │     └────────────────▶ OrcaDispatcher ─────┘   snapshots   SSE/REST   (security) │   WebGL2 control room
 City: Manhattan streets │                          │   ▲                                                  │   React home page
 (OSM) or synthetic grid │     GPS pings ──▶ FeatureStore ──▶ ETA / acceptance / demand models              │
                         └────────────────────────────────────────────────────────────────────────────────┘
```

Both dispatchers run on one world: identical riders, traffic, incidents and drivers. Every
difference in outcome comes from the dispatch decisions alone.

## The city

`orca/city.py` defines one API (`route`, `cruise`, `snap`, `zone_of`, `road_metrics`, ...) with
two implementations; one is active per process (`city.use(name)`, `ORCA_CITY`, `--city`):

| | `StreetCity` (production) | `GridCity` (tests, fallback) |
|---|---|---|
| Roads | Manhattan's drivable streets from OpenStreetMap: 4,454 intersections, 9,598 directed segments, 939 km, one-way rules, 13 road classes | 28 × 28 grid, arterials every seventh road |
| Zones | the 63 official TLC taxi zones (rasterised lookup) | 7 × 7 squares |
| Routing | fastest path by free-flow time (Dijkstra on the reversed graph, cached per destination, under 1 ms) | L-shaped, preferring arterials |
| Speeds | real median taxi speed for the hour × road-class factor, calibrated against real trip times | synthetic, with drifting congestion |

Both the Python and the browser engine implement the same API (`orca/streets.py`,
`frontend/src/cities.js`), so a ride in either takes the same streets.

### Building the street city

```
OpenStreetMap drivable ways ──┐                                   NYC Open Data building footprints
TLC taxi-zone shapefile ──────┼─▶ scripts/build_streets.py          (45,172 in Manhattan, surveyed heights)
                              │     · project WGS84 → NY State Plane (EPSG:2263, own LCC implementation, orca/geo.py)
                              │     · keep nodes inside the 63 Manhattan zone polygons
                              │     · oneway tags → directed edges; contract pass-through nodes
                              │     · keep the largest strongly connected component
                              │     · rotate by the 29° street grid → world units (1 unit = 30 m)
                              ▼                                              │
   artifacts/streets_manhattan.npz, frontend/src/streets_data.js            ▼
                                                        scripts/build_buildings.py: minimum-area rotated rectangle per
                                                        footprint (rotating calipers) → frontend/src/buildings_data.js
```

The projection is checked in the tests: the Empire State Building, Central Park, One WTC and the
Apollo Theater land in their correct taxi zones. A test also checks that every route hop is a
directed street segment (one-way rules respected) and that each route is the shortest path.

### Calibration against reality

`python -m orca.calibrate` routes 3,000 real January 2015 trips through the network at that hour's
real median taxi speed and compares simulated with real durations. Uncalibrated, the median
simulated trip was 9.9 min against 10.2 min in reality; the fitted speed scale (0.98) makes the
medians match. The per-trip correlation (0.68) is limited by the TLC publishing zones rather than
exact points.

## Real data pipeline

```
NYC TLC yellow_tripdata_2015-01.parquet (12.7M trips, zone IDs)      taxi_zones.shp (official polygons)
                     └──────────────▶ scripts/prepare_tlc.py ◀──────────────────┘
                          Manhattan→Manhattan, ordinary weekdays (17 days, 6.2M trips);
                          each trip at a random point inside its real pickup / dropoff zone polygon,
                          stored in absolute State Plane metres
          ┌──────────────────────────────┼─────────────────────────────────┐
          ▼                              ▼                                 ▼
 data/nyc_tlc_2015-01_manhattan   artifacts/nyc_traffic_profile.json   frontend/src/tlc_data.js
 .parquet (2M-trip replay)        real median speed + trips by hour     24k-trip sample, in the street city's frame
```

* **Demand.** The replay folds all days into one typical weekday and keeps the real time-of-day and
  spatial pattern. Its rate is sized from reality: a car is busy for the real mean trip duration in
  the peak hour plus about five minutes to reach the rider, and the peak keeps 80% of the fleet busy.
* **Traffic.** Citywide road speed for each hour is the real median taxi speed (trip distance /
  duration) for that hour. Accidents slow a local area on top of it.
* **Positions.** Trips are projected with exactly the transform the streets were built with, so a
  pickup in Hell's Kitchen happens on a Hell's Kitchen street.

## Request lifecycle

1. A request enters both dispatchers at the same moment, snapped to the nearest intersection.
2. **Nearest** offers it at once to the idle car closest in a straight line to its last reported
   position, ignoring how old that position is.
3. **ORCA** holds it until the next 3-second batch. For each waiting request it takes the K nearest
   idle cars with fresh GPS, predicts each one's drive time by road, scores every pair and solves the
   batch with `scipy.optimize.linear_sum_assignment`. Above 150 waiting requests it falls back to a
   greedy pass so a burst can never stall the loop.
4. The driver answers after 4-11 s. A decline or a 20 s silence sends the request back to the queue,
   excluding that car.
5. Riders cancel after 15 minutes without pickup.

Driver decisions are a deterministic hash of (car, request, attempt), so both dispatchers face
identical drivers. Idle cars cruise by turning onto a random outgoing street at each intersection.

## Score

```
score = predicted drive time
      + (1 − P(accept)) × REOFFER              expected cost of a declined / expired offer
      + min(ZONE_CAP, (shortfall − 1) × ZONE_SLOPE)   for taking a scarce car from a zone about to get busy
priority: urgent requests × URGENT_W, minus WAIT_W per second already waited (capped at 300 s)
```

The weights live in `orca/config.py`. `python -m orca.tune` searches them by successive halving
(many candidates on short runs, the best third on more seeds, a final round against the hand-set
defaults as a control) on seeds 101-109, which the benchmark never uses, and writes
`artifacts/dispatch_params_<city>.json`. `frontend/build.py` injects the same weights into the
browser engine.

## Models

| Model | Input | Training | Fallback |
|---|---|---|---|
| ETA | road distance along the fastest path, share on major roads, straight-line distance, observed zone speeds at origin / corner / destination, city speed, hour | pickup legs logged from six runs that start at different times of day, with frequent incidents; time split inside each run; learns a log correction to distance / observed speed, so it extrapolates to traffic slower than any it saw | distance over observed zone speed |
| Acceptance | per-driver offers and accepts | online Beta-Bernoulli posterior, updated on every answer | the prior |
| Demand | per-zone counts in the last 1-5 min, 15-min mean, city totals, time of day, zone centre | real per-minute pickups in each of the 63 taxi zones over 17 weekdays, Poisson loss, time split | EWMA with trend |
| Real trip duration (validation) | pickup and dropoff zone, distance, time of day, weekday | 1.5M real trips from earlier days, tested on 482k later ones | historical median for the zone pair and hour |

Models only see what a real system could observe (GPS pings and request history), never the
simulator's hidden traffic field. Artifacts are per city (`eta_manhattan.joblib`, ...).

## Benchmark methodology

`python -m orca.bench` runs every policy on 10 seeds × 2 scenarios in parallel, plus a full 24-hour
day. Results are means over seeds with 95% percentile-bootstrap intervals, the paired per-seed
reduction against nearest-car dispatch, and Wilcoxon signed-rank tests for the ablations (learned vs
heuristic models, tuned vs hand-set weights).

## Serving and security

`orca/security.py` is pure ASGI middleware (it never buffers the SSE stream):

* **Access.** Anyone can watch and trigger incidents. Controls that change the city for every viewer
  (speed, pause, demand, fleet size) are operator actions: they need `Authorization: Bearer
  $ORCA_ADMIN_TOKEN` when a token is set, or come from loopback in local development.
* **Limits.** Per-client token buckets (commands, resets, explain, API), a 4 KB body limit, caps on
  concurrent SSE streams per client and in total.
* **Headers.** Content-Security-Policy, nosniff, frame and referrer policies, request ids.
* **Observability.** JSON access log, Prometheus counters per route and status class at `/metrics`.

## Delivery

* **CI** (`.github/workflows/ci.yml`): ruff, ESLint, TypeScript; unit, real-data and browser
  (Playwright) tests; the Docker image is built and smoke-tested; the dependency locks must be current.
* **Locks**: `backend/requirements*.lock`, generated from the tested environment by `tools/lock_deps.py`.
* **Docker**: multi-stage (real data, home page, runtime), non-root, health check. Render and Fly.io
  configs are included.
* **Large files** go through Git LFS.

## Scaling this out

At real scale the single process would split along the same seams:

* **Ingest**: GPS pings and requests into Kafka (or Redpanda), partitioned by zone.
* **Routing**: a contraction-hierarchy or OSRM service for city-wide shortest paths; per-request
  trees are cheap here (4,454 nodes) but not on a metro-area graph.
* **Location index**: H3 cells in Redis, replacing the bucket grid.
* **Feature store**: streaming aggregation (Flink) writing zone speeds and demand counts to Redis.
* **Matching**: one stateless matcher per zone cluster, with a boundary pass for requests near edges.
* **Models**: served in the matcher process, retrained nightly, promoted after shadow evaluation.

## Frontend

| Path | What | Built with |
|---|---|---|
| `/` | home page: what ORCA does, live engine status, benchmark with confidence intervals, real-data charts, the narrated tour | `web/`: React 18, TypeScript, Vite, Tailwind CSS, Framer Motion; numbers imported at build time from `docs/benchmark.json` and `backend/artifacts/*.json` |
| `/app` | the control room | `frontend/`: hand-written WebGL2, bundled by `frontend/build.py` |

The control room is one self-contained file:

| File | Role |
|---|---|
| `streets_data.js`, `buildings_data.js`, `tlc_data.js` | generated: the street network and zones, the skyline, and a real-trip sample |
| `sim.js` | the dispatch engine in JavaScript, used when no server is reachable |
| `cities.js` | `StreetCity` (Dijkstra on a binary heap, spatial hash) and `GridCity` |
| `gl.js` | WebGL2 renderer: instanced meshes, a sun shadow map that follows the camera (static casters re-rendered only when the view moves far), screen-space lines, water, sky, ACES tone mapping, orbit camera |
| `city.js` | the 3D city: land triangulated from the zone polygons, street ribbons along the real geometry, 44,693 buildings as rotated boxes at surveyed heights, Central Park |
| `app.js` | data sources, frame interpolation, distance-adaptive car size, real zone outlines and an SVG zone map, the console and the guided tour |

The tour's capture mode (`index.html?capture`) steps the page frame by frame;
`tools/make_tour_video.py` records it, voices it from the captions actually shown, composes an
original score, mixes and encodes MP4 and WebM.
