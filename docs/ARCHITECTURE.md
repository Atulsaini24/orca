# Architecture

```
                         ┌──────────────────────────── one process today ─────────────────────────────┐
 real trip replay ───────┼─▶ World ──requests──▶ NearestDispatcher ──┐                                  │
 (or synthetic demand)   │     │ real traffic by zone and hour,       ├─▶ Engine ─▶ Starlette ─▶ middleware ─┼─▶ browser
                         │     │ incidents                            │   snapshots   SSE/REST   (security) │   WebGL2 control room
 City: a registered real │     └────────────────▶ OrcaDispatcher ─────┘                                  │   React home page
 city or the test grid   │     GPS pings ──▶ FeatureStore ──▶ ETA / acceptance / demand models              │
                         └────────────────────────────────────────────────────────────────────────────────┘
```

Both dispatchers run on one world: identical riders, traffic, incidents and drivers. Every
difference in outcome comes from the dispatch decisions alone.

## Cities

`orca/cityspec.py` is the registry. A city is: its working projection (the frame of its official zone
shapefile), the angle of its street grid, its forecasting zones, a bounding box for OpenStreetMap, and
its trip records. Everything downstream is built per city from that entry.

| | Manhattan | Chicago (downtown core) |
|---|---|---|
| Projection | NY State Plane Long Island, EPSG:2263 (Lambert conformal conic) | Illinois State Plane East, EPSG:3435 (transverse Mercator) |
| Street grid | 29° east of north | north-aligned |
| Streets (OpenStreetMap) | 4,454 intersections, 9,598 directed segments, 939 km, 65% one-way | 3,914 intersections, 9,930 directed segments, 819 km, 29% one-way |
| Zones | 63 official TLC taxi zones | 25 official neighbourhoods in the 7 core community areas |
| Buildings | 44,375 from NYC Open Data, surveyed roof heights | 55,784 from the City of Chicago, heights from storeys |
| Trips, September 2026 | NYC TLC: yellow taxis + Uber / Lyft (high-volume for-hire), 5.5M weekday trips | City of Chicago Transportation Network Providers (Uber, Lyft, Via) |
| Surroundings | island in rivers | mainland, Lake Michigan to the east |

Both projections are implemented in `orca/geo.py` (Snyder, *Map Projections: A Working Manual*) and
checked in the tests against landmarks: the Empire State Building, Central Park, One WTC and the
Apollo Theater land in their correct taxi zones; Willis Tower, Navy Pier, Wrigley Field, the United
Center and Lincoln Park Zoo in their correct Chicago neighbourhoods.

### The city API

`orca/city.py` defines one API (`route`, `cruise`, `snap`, `zone_of`, `road_metrics`, `traffic`, ...)
with two implementations, one active per process (`city.use(name)`, `ORCA_CITY`, `--city`):

* **`StreetCity`** (`orca/streets.py`, and `frontend/src/cities.js` for the browser): a real street
  network. Fastest paths by free-flow time (Dijkstra on the reversed graph, cached per destination,
  under 1 ms); parallel segments between the same intersections keep the fastest. Riders are picked up
  only at intersections touching an ordinary street, never on an expressway.
* **`GridCity`**: a 28 × 28 synthetic grid with 7 × 7 zones, for unit tests and as a fallback.

### Building a city

```
OpenStreetMap drivable ways ─┐                                   official building footprints
official zone polygons ──────┼─▶ scripts/build_streets.py --city    (NYC Open Data / City of Chicago)
OpenStreetMap parks ─────────┘     · project to the city's state plane, keep nodes inside its zones
                                   · oneway tags → directed edges; contract pass-through nodes
                                   · largest strongly connected component
                                   · rotate by the street grid → world units (1 unit = 30 m)
                                   · bridges over water and parks for the renderer
                                   ▼                                         │
   artifacts/streets_<city>.npz, frontend/data/<city>/streets.js             ▼
                                         scripts/build_buildings.py --city: real outlines, simplified
                                         (Douglas-Peucker, 3 m) → frontend/data/<city>/buildings.js
```

### Traffic: per zone and per hour

`scripts/prepare_trips.py` measures speed (trip distance over duration) from each city's own trips:
the citywide median for every hour, and a zone × hour table from short trips (≤ 2.5 miles) credited
to both their zones and shrunk toward the city value (150 pseudo-trips) so thin zones stay stable.
Cars move at the base speed × the road class factor × the speed of the zone they are in at that hour,
so Midtown at 6 pm is slower than Harlem at 6 pm. Accidents slow a local area on top of it.

### Calibration against reality

`python -m orca.calibrate --city <city>` routes 3,000 real weekday trips through the network the way
the simulator moves cars (local zone speed, road classes) and fits the single speed scale that makes
the median simulated trip as long as the median real one. In Manhattan (September 2026) the median
real trip is 15.2 min and the fitted scale 0.90; simulated and real trip times correlate at 0.73
per trip. Results are in `artifacts/speed_calibration_<city>.json`.

## Real data pipeline

```
NYC TLC (yellow + Uber/Lyft) parquet ─┐                  City of Chicago TNP trips ─┐
TLC taxi-zone shapefile ──────────────┼─▶ scripts/prepare_trips.py --city ◀─────────┘
                                         ordinary weekdays (Labor Day excluded), both ends inside the zones;
                                         positions: random point in the real zone (NYC), tract centre + 120 m
                                         jitter (Chicago), in absolute state-plane metres
          ┌──────────────────────────────┼─────────────────────────────┬──────────────────────────────┐
          ▼                              ▼                             ▼                              ▼
 data/trips_<city>.parquet   artifacts/traffic_<city>.json   artifacts/real_waits_manhattan.json   frontend/data/<city>/trips.js
 (2M-trip replay)            hourly + zone x hour speeds      real Uber / Lyft rider waits         24k-trip browser sample
```

* **Demand.** The replay folds all days into one typical weekday, keeping the real time-of-day and
  spatial pattern. Its rate is sized from reality: a car is busy for the real mean trip duration in
  the peak hour plus about five minutes to reach the rider, and the peak keeps 80% of the fleet busy.
* **Reality check.** For every Uber and Lyft trip the TLC records when the rider asked and when the
  driver was on scene: in Manhattan, September 2026, 4:00 on average over 3.4M rides. The home page
  sets the simulation's waits next to it.

## Request lifecycle

1. A request enters both dispatchers at the same moment, snapped to the nearest street intersection.
2. **Nearest** offers it at once to the idle car closest in a straight line to its last reported
   position, ignoring how old that position is.
3. **ORCA** holds it until the next 3-second batch. For each waiting request it takes the K nearest
   idle cars with fresh GPS, predicts each one's drive time by road, scores every pair and solves the
   batch with `scipy.optimize.linear_sum_assignment`. Above 150 waiting requests it falls back to a
   greedy pass so a burst can never stall the loop.
4. The driver answers after 4-11 s. A decline or a 20 s silence sends the request back to the queue.
5. Riders cancel after 15 minutes without pickup.

Driver decisions are a deterministic hash of (car, request, attempt), so both dispatchers face
identical drivers. Idle cars cruise by turning onto a random outgoing street at each intersection.

## Score

```
score = predicted drive time
      + (1 − P(accept)) × REOFFER                          expected cost of a declined / expired offer
      + min(ZONE_CAP, (shortfall − 1) × ZONE_SLOPE)        for taking a scarce car from a zone about to get busy
      + DEADLINE_W × 600 / (1 + exp(slack / 40))           give-up guard, slack = 15 min − (waited + drive time + 30 s)
priority: urgent requests × URGENT_W, minus WAIT_W per second already waited (capped at 300 s)
```

The give-up guard exists because averages hide abandonment: without it a car arriving at minute 16
scores almost like one arriving at minute 14, though only one of them finds the rider still there.
The benchmark keeps an ablation with the guard switched off to measure it.

The weights live in `orca/config.py`. `python -m orca.tune` searches them by successive halving on
seeds 101-109, which the benchmark never uses, and writes `artifacts/dispatch_params_<city>.json`;
`frontend/build.py` puts the same weights into each city's browser config.

## Models

| Model | Input | Training | Fallback |
|---|---|---|---|
| ETA | road distance along the fastest path, share on major roads, straight-line distance, observed zone speeds at origin / corner / destination, city speed, hour | pickup legs logged from six runs starting at different times of day, with frequent incidents; time split inside each run; learns a log correction to distance / observed speed, so it extrapolates to traffic slower than any it saw | distance over observed zone speed |
| Acceptance | per-driver offers and accepts | online Beta-Bernoulli posterior, updated on every answer | the prior |
| Demand | per-zone counts in the last 1-5 min, 15-min mean, city totals, time of day, zone centre | real per-minute pickups in each zone over the month's weekdays, Poisson loss, time split | EWMA with trend |
| Real trip duration (validation) | pickup and dropoff zone, distance, time of day, weekday | real trips from earlier days, tested on later ones | historical median for the zone pair and hour |

Models only see what a real system could observe (GPS pings and request history), never the
simulator's hidden traffic field. Every artifact is per city (`eta_<city>.joblib`, ...).

## Benchmark methodology

`python -m orca.bench --city <city>` runs every policy on 10 seeds × 2 scenarios in parallel, plus a
full 24-hour day. Results are means over seeds with 95% percentile-bootstrap intervals, the paired
per-seed reduction against nearest-car dispatch, and Wilcoxon signed-rank tests for the ablations
(learned vs heuristic models, tuned vs hand-set weights, give-up guard on vs off for riders lost).

## Serving and security

`orca/security.py` is pure ASGI middleware (it never buffers the SSE stream):

* **Access.** Anyone can watch and trigger incidents. Controls that change the city for every viewer
  (speed, pause, demand, fleet size, switching city) are operator actions: they need `Authorization:
  Bearer $ORCA_ADMIN_TOKEN` when a token is set, or come from loopback in local development.
* **Limits.** Per-client token buckets, a 4 KB body limit, caps on concurrent SSE streams.
* **Headers.** Content-Security-Policy (no `eval`), nosniff, frame and referrer policies, request ids.
* **Observability.** JSON access log, Prometheus counters per route and status class at `/metrics`.

## Delivery

* **CI** (`.github/workflows/ci.yml`): ruff, ESLint, TypeScript; unit, real-data, browser and visual
  tests; the Docker image is built and smoke-tested; the dependency locks must be current.
* **Visual tests** (`backend/tests/test_visual.py`): every built city is rendered by day and by night
  in the deterministic capture mode and checked for structure, contrast, night darkness and lit
  windows, then compared with golden images kept per platform and GPU renderer.
* **Locks**: `backend/requirements*.lock`, generated from the tested environment by `tools/lock_deps.py`.
* **Docker**: multi-stage (real data for every city, home page, runtime), non-root, health check.
  Render and Fly.io configs are included. Large files go through Git LFS.

## Scaling this out

At real scale the single process would split along the same seams:

* **Ingest**: GPS pings and requests into Kafka (or Redpanda), partitioned by zone.
* **Routing**: a contraction-hierarchy or OSRM service; per-request trees are cheap at 4,000 nodes but
  not on a metro-area graph.
* **Location index**: H3 cells in Redis, replacing the bucket grid.
* **Feature store**: streaming aggregation (Flink) writing zone speeds and demand counts to Redis.
* **Matching**: one stateless matcher per zone cluster, with a boundary pass for requests near edges.
* **Models**: served in the matcher process, retrained nightly, promoted after shadow evaluation.
* **Cities**: one engine per city; the registry already keeps them independent.

## Frontend

| Path | What | Built with |
|---|---|---|
| `/` | home page: every city's results with confidence intervals, a reality check against real Uber / Lyft waits, real-data charts, the narrated tour | `web/`: React 18, TypeScript, Vite, Tailwind CSS, Framer Motion; numbers imported at build time from `docs/benchmark_<city>.json` and `backend/artifacts/*.json` |
| `/app?city=<city>` | the control room | `frontend/`: hand-written WebGL2, bundled by `frontend/build.py` |

The control room page is 200 KB of code; at start-up it loads only the chosen city's data from
`frontend/data/<city>/` (`streets.js`, `buildings.js`, `trips.js`, `config.js`).

| File | Role |
|---|---|
| `sim.js` | the dispatch engine in JavaScript, used when no server is reachable |
| `cities.js` | `StreetCity` (Dijkstra on a binary heap, spatial hash, zone x hour traffic) and `GridCity` |
| `gl.js` | WebGL2 renderer: instanced and tiled static meshes; facade materials for real footprints (glass curtain walls that mirror the sky, masonry with punched windows, shop fronts, cornices, anti-aliased with screen-space derivatives; warm lit windows and tower crowns at night); a sun shadow map that follows the camera; drifting cloud shadows; a sky with sun and stars; marching-dash lines; lamps; water; ACES tone mapping; orbit camera |
| `city.js` | the 3D city: land from the zone polygons (or the mainland and a lake), street ribbons along the real geometry, bridges with piers, parks with soft-crowned trees that sway, every building extruded from its real outline with a rooftop bulkhead or water tank, and the car models (detailed near, simple far) |
| `app.js` | data sources, day and night from the city clock, frame interpolation paced to the server's frame gap, distance-adaptive car size, headlamp pools at night, flowing pickup routes, real zone outlines and an SVG zone map, a city switcher, the console and the guided tour |

### Rendering budget

The target is 60 frames per second on integrated graphics. Measured on Intel Iris Xe on a 1.5x screen
with `make perf`: 27 fps before this design; with it and the full facade shading, about 50 fps over
the whole city (dynamic resolution settles near a 1.1x pixel ratio there) and 60-70 fps zoomed in.

* **Tiles.** Land, streets, bridges and the 1.8-million-index building mesh are reordered once at
  start-up into 32-unit (about 1 km) ground tiles, each with its index range and bounding box. Every
  frame, tiles outside the view frustum or beyond the fog are skipped and the rest are drawn front to
  back, so hidden pixels fail the depth test before the facade shader runs.
* **Level of detail.** Buildings under 24 m are skipped in tiles more than 260 units (about 8 km)
  away; cars switch to a body-and-glasshouse model beyond 240 units of camera distance.
* **Shadows.** Only static geometry casts shadows, into one 2048² map that is redrawn only when its
  key changes: the sun direction (quantised to about 0.3 degrees) or the shadowed area, which follows
  the camera in 30-unit steps. Surfaces facing away from the sun skip the lookup.
* **Overdraw.** Sky and water are drawn after the opaque meshes, so they shade only uncovered pixels;
  window patterns, sun glints and lit-window hashes are computed only where a window is at least about
  a pixel wide, and walls farther away use the windows' mean. Flat ground shares vertices.
* **Dynamic resolution.** When frames take over 21 ms for a second, the pixel ratio drops in 0.2 steps
  (to at least 55% of the screen's); after several fast seconds it climbs back, waiting longer after
  each fall so it settles instead of oscillating.
* **Start-up.** The city builds in about 0.5 s (Manhattan) to 0.7 s (Chicago); `performance.measure`
  entries named `orca-*` show each phase. City data is gzip-compressed by the server.

The tour's capture mode (`index.html?capture&city=…&light=…`) steps the page frame by frame;
`tools/make_tour_video.py` records it, voices it from the captions actually shown, composes an
original score, mixes and encodes MP4 and WebM.
