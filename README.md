# ORCA: real-time ride dispatch on real cities

ORCA decides which car takes each ride request, live, for a fleet of 1,200 cars driving the **real
street networks** of **Manhattan** and **Chicago** (OpenStreetMap, one-way rules included), past
**100,000 real buildings** extruded from official footprints, serving **real ride demand** from
September 2026: NYC taxi and Uber / Lyft trips, and Chicago's Uber / Lyft / Via trips. It runs head
to head against the standard "send the nearest car" rule on the **same riders, traffic, incidents and
drivers**, so every difference in the results comes from the dispatch decisions alone.

Every 3 seconds ORCA scores each (rider, car) pair on

* **drive time by road**, from a gradient-boosted ETA model on top of the fastest path through the street network,
* **acceptance**, a per-driver Beta-Bernoulli estimate learned online from offers,
* **GPS freshness**, so it never offers rides to cars that went silent,
* **zone supply**, from a demand forecaster over each city's official zones,
* **the rider's deadline**, so a car that arrives after the rider would give up is not preferred,

then solves the whole batch as a min-cost assignment (Hungarian algorithm), and moves idle cars
toward zones forecast to run short.

The control room is a **hand-written WebGL2 renderer** (no 3D library) with day and night lighting
from the city clock; the **home page** is React, TypeScript, Vite and Tailwind; a **narrated tour**
with an original score is in [`docs/orca-tour.mp4`](docs/orca-tour.mp4).

## Results

45 simulated minutes per run, 1,200 cars per dispatcher, **10 seeds**, means with **95% bootstrap
confidence intervals**, real September 2026 demand and real traffic by zone and hour. Full tables,
paired significance tests and a 24-hour day: [`docs/benchmark_manhattan.md`](docs/benchmark_manhattan.md),
[`docs/benchmark_chicago.md`](docs/benchmark_chicago.md); reproduce with `make bench`.

| Mean wait, request to pickup | Nearest car | ORCA, heuristic models | **ORCA, learned models** |
|---|---:|---:|---:|
| Manhattan, steady demand | 136 s | 122 s | **109 s** (−19%, CI 17-22%) |
| Manhattan, spike + GPS outage + accident | 186 s | 163 s | **152 s** (−18%, CI 14-21%) |
| Manhattan, full 24-hour day | 171 s | | **112 s** (−35%) |
| Chicago, steady demand | 97 s | 88 s | **81 s** (−15%, CI 12-19%) |
| Chicago, spike + GPS outage + accident | 118 s | 106 s | **99 s** (−16%, CI 13-19%) |
| Chicago, full 24-hour day | 116 s | | **79 s** (−32%) |

| Riders who gave up (15 min) | Nearest car | ORCA, heuristic | **ORCA, learned** |
|---|---:|---:|---:|
| Manhattan, under stress | 13.4 | 7.0 | **5.3** |
| Chicago, under stress | 0.3 | 0.0 | **0.0** |
| Chicago, full 24-hour day | 81 | | **1** |

![benchmark](docs/benchmark_manhattan.png)
![benchmark, Chicago](docs/benchmark_chicago.png)

* **Learned models** beat the heuristic fallbacks on mean wait in both cities and every scenario
  (Wilcoxon p = 0.002), and lose the fewest riders under stress in Manhattan.
* **Tuned weights** (successive halving on seeds the benchmark never sees, separately per city) beat the
  hand-set ones in Manhattan in both scenarios (p ≤ 0.004) and in Chicago under stress (p = 0.01); in
  Chicago's steady scenario the gain (84 s to 81 s) is not significant (p = 0.06).
* **The give-up guard** is kept by the tuner, but switching it off changes riders lost only within
  noise (5.7 vs 5.3 in Manhattan, p = 0.5): the stress-test improvement comes from the retrained models
  and tuned weights. Chicago's core has fewer riders per car, so almost no one gives up in a 45-minute
  run with either dispatcher; over a full day the nearest-car rule loses 81 riders, ORCA 1.

### Reality check

For every Uber and Lyft trip the TLC records when the rider asked and when the driver arrived. Across
3.4 million Manhattan rides in September 2026 the driver was on scene after **3:59 on average** (p90
8:04). The simulation's waits (1:49 with ORCA, 2:16 with nearest car) are the same order of magnitude;
it is not a like-for-like race, since the platforms run thousands of drivers and the simulation 1,200
cars on a sampled share of the demand.

### Models (held-out data, later in time than training)

| Mean absolute error | Manhattan: baseline → **ORCA** | Chicago: baseline → **ORCA** |
|---|---:|---:|
| Pickup ETA on real streets (baseline: zone-speed heuristic) | 42.6 s → **34.7 s** | 34.8 s → **31.3 s** |
| Rides per zone, next 5 min (baseline: EWMA with trend) | 1.25 → **0.75** | 2.08 → **1.27** |
| Real trip duration, later days (baseline: zone-pair median) | 236 s → **222 s** | 189 s → **166 s** |

Simulated trip times are calibrated against reality per city: the median routed trip matches the
median real one (Manhattan 15.2 min, Chicago 11.6 min), with per-trip correlations of 0.73 and 0.72.

## Run it

```bash
make install        # Python deps from backend/requirements-dev.lock, home-page deps (npm ci)
make data           # download and prepare September 2026 trips for both cities
make web            # build the home page
make run            # http://localhost:8000 (home page), http://localhost:8000/app (control room)
```

Or `docker compose up --build`: the image build downloads and prepares every city's real data.

Street networks, skylines and zone boundaries are committed; rebuild them with `make cities`.
Open a city directly with `/app?city=chicago`, or switch in the control room. Run the synthetic test
grid with `--city grid`.

The control room also works on its own (open `frontend/index.html`): it then runs the same engine in
the browser on the same streets with a 24,000-trip real-data sample, loading only the chosen city.

In the control room: drag to rotate, right-drag to pan, scroll to zoom; `W A S D`, `Q E`, `R F`.
Click any ring to see why ORCA chose that car. `?light=night` pins the lighting; by default it
follows the city clock.

### Operator controls

Anyone can watch and trigger incidents. Speed, pause, demand level, fleet size and switching city
change the simulation for every viewer, so they are operator-only: set `ORCA_ADMIN_TOKEN` and open
`/app?token=…` once, or run locally (loopback is trusted). `ORCA_OPEN_CONTROLS=1` opens them to
everyone, which `docker-compose.yml` does for local use.

## Real data

| City | What | Source |
|---|---|---|
| both | drivable streets, parks | [OpenStreetMap](https://www.openstreetmap.org) (ODbL) |
| Manhattan | 5.5M weekday trips: yellow taxis, Uber, Lyft (Sept 2026); 63 taxi zones | [NYC TLC trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) |
| Manhattan | 44,375 building footprints with surveyed heights | [NYC Open Data, BUILDING](https://data.cityofnewyork.us/d/5zhs-2jue) |
| Chicago | 1.2M weekday Uber / Lyft / Via trips in the core (Sept 2026) | [City of Chicago, TNP trips](https://data.cityofchicago.org/d/6dvr-xwnh) |
| Chicago | 25 neighbourhoods, 7 community areas; 55,784 building footprints with storeys | City of Chicago Data Portal |

Each city uses its own state-plane projection (implemented in `orca/geo.py`): New York Long Island
(EPSG:2263) and Illinois East (EPSG:3435). Streets, buildings and trips share one transform per city.

## Train, tune, benchmark

```bash
make train          # per city: demand, ETA, real-trip model, speed calibration
make tune           # per city: scoring weights by successive halving on seeds 101-109
make bench          # per city: 10 seeds x 2 scenarios + a full day, CIs, significance tests, ablations
make test           # unit, both real cities, real data, security, browser end-to-end and visual tests
make lint           # ruff, ESLint, TypeScript
make video          # record, narrate (neural voice), score and encode the tour
make perf           # frame rate and GPU time of the control room on this machine
```

## API

| Method | Path | |
|---|---|---|
| GET | `/` | home page |
| GET | `/app?city=` | control room |
| GET | `/app/data/{city}/…` | a city's streets, buildings, trip sample and browser config |
| GET | `/media/{file}` | tour video (MP4, WebM), captions, poster, score |
| GET | `/api/health` | engine status, city, which models are loaded |
| GET | `/api/stream?view=smart\|base` | Server-Sent Events: `frame` at 10 Hz, `summary` at 1 Hz |
| POST | `/api/command` | `{"cmd": "spike" \| "outage" \| "accident"}`; operator: `"speed" \| "pause" \| "demand"` with `"value"` |
| POST | `/api/reset` | operator: `{"fleet": 1200, "seed": 7, "city": "chicago"}` |
| GET | `/api/explain/{request_id}` | why ORCA chose that car, every candidate's score, the request's timeline |
| GET | `/api/analytics?since=60` | wait statistics from the SQLite trip ledger: percentiles, per zone, per hour |
| GET | `/api/trips?limit=50` | most recent pickups |
| GET | `/api/models` | training metrics of the models in use, and the latest benchmark |
| GET | `/metrics` | Prometheus: dispatch metrics and HTTP requests by route and status |

## Engineering

* **CI** on every push: lint, type-check, unit + real-data + browser + visual-regression tests, Docker
  build and smoke test, lock freshness ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
* **Reproducible**: exact dependency locks, fixed seeds, data pipelines as scripts, large files in Git LFS.
* **Secure by default**: operator-only global controls, rate limits, body and stream limits, a CSP
  without `eval`, non-root container.
* **Fast on integrated graphics**: the 100,000-building city is split into 1 km tiles that are culled
  against the view and drawn front to back, low-rise buildings and cars switch to lighter models with
  distance, the shadow map is redrawn only when the sun or the view moves, and the resolution adapts
  to keep frames under about 21 ms. City data is served gzip-compressed with cache revalidation.
* **Deployable**: Docker multi-stage image with health check; [`render.yaml`](render.yaml) and [`fly.toml`](fly.toml).

Design notes, the data pipelines and how this would scale out: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Honest limits

* Streets, buildings, demand and traffic speeds (by zone and hour) are real. Individual car movement
  along the streets and driver behaviour (acceptance, response time) are simulated: no public dataset
  has live GPS traces.
* Trip records give zones (NYC) or census-tract centres (Chicago) rather than exact points; trips are
  placed within them. Chicago's timestamps are rounded to 15 minutes for privacy and spread within.
* Chicago building heights come from storey counts (3.6 m per storey), not surveyed heights.
* The simulation uses 1,200 cars per dispatcher on a sampled share of the demand, so its wait times
  are comparable with reality in magnitude, not in level.
* The ETA model learns from simulated pickup legs; the real-trip model validates the approach on real data.
* One process holds one city's state in memory; [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) describes
  the split into services at real scale.
