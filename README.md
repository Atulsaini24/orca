# ORCA: real-time ride dispatch on Manhattan's real streets

ORCA decides which car takes each ride request, live, for a fleet of 1,200 cars driving
**Manhattan's real street network** (OpenStreetMap, one-way rules included) past **44,693 real
buildings**, serving **real New York taxi demand** (NYC TLC trip records). It runs head to head
against the standard "send the nearest car" rule on the **same riders, traffic, incidents and
drivers**, so every difference in the results comes from the dispatch decisions alone.

Every 3 seconds ORCA scores each (rider, car) pair on

* **drive time by road**, from a gradient-boosted ETA model on top of the fastest path through the street network,
* **acceptance**, a per-driver Beta-Bernoulli estimate learned online from offers,
* **GPS freshness**, so it never offers rides to cars that went silent,
* **zone supply**, from a demand forecaster over the 63 official taxi zones,

then solves the whole batch as a min-cost assignment (Hungarian algorithm), and moves idle cars
toward zones forecast to run short.

The control room is a **hand-written WebGL2 renderer** (no 3D library); the **home page** is
React, TypeScript, Vite and Tailwind; a **narrated 100-second tour** with an original score is in
[`docs/orca-tour.mp4`](docs/orca-tour.mp4).

## Results

Real Manhattan streets, real January 2015 weekday demand, real hourly traffic speed. 45 simulated
minutes per run, 1,200 cars per dispatcher, **10 seeds**, means with **95% bootstrap confidence
intervals**. Full tables, paired significance tests and a full 24-hour day in
[`docs/benchmark.md`](docs/benchmark.md); reproduce with `make bench`.

| | Nearest car | ORCA, heuristic models | **ORCA, learned models** |
|---|---:|---:|---:|
| Steady demand, mean wait | 286 s | 162 s | **150 s** (−47%, CI 45-49%) |
| Steady demand, riders who gave up | 49.5 | 4.6 | **3.2** |
| Spike + GPS outage + accident, mean wait | 319 s | 227 s | **206 s** (−35%, CI 32-39%) |
| Same, riders who gave up | 279 | **88** | 110 |
| Full 24-hour day, mean wait | 210 s | | **107 s** (−49%) |

![benchmark](docs/benchmark.png)

* Learned models beat the heuristic fallbacks on mean wait in both scenarios (Wilcoxon p = 0.002).
  Under stress they cut waits but lose more riders than the heuristics, a trade-off worth knowing.
* Weights tuned on separate seeds help under stress (p = 0.01) and make no difference on a steady day.

| Model (held-out data, later in time than training) | Baseline | Baseline error | ORCA error |
|---|---|---:|---:|
| Pickup ETA on real streets (gradient boosting on a physics baseline) | observed zone-speed heuristic | 47.8 s | **31.9 s** |
| Rides per taxi zone, next 5 min (Poisson GBM, real pickups) | EWMA with trend | 1.41 | **0.85** |
| Real trip duration, 482k later NYC trips | historical median per zone pair and hour | 191 s | **182 s** |

Simulated trip times are calibrated against reality: routed through the street network, the median
real trip (10.2 min) and the median simulated one match after a 0.98 speed scale.

## Run it

```bash
make install        # Python deps from backend/requirements-dev.lock, home-page deps (npm ci)
make data           # download the 12.7M-trip TLC file + zone shapefile, prepare the replay (~1 min)
make web            # build the home page
make run            # http://localhost:8000 (home page), http://localhost:8000/app (control room)
```

Or `docker compose up --build`: the image build downloads and prepares the real data itself.

The street network and skyline are committed (`backend/artifacts/streets_manhattan.npz`,
`frontend/src/*_data.js`); rebuild them with `python scripts/build_streets.py --download` and
`python scripts/build_buildings.py --download`. Run the synthetic grid city instead with
`--city grid` (or `?city=grid` in the browser).

The control room also works on its own (open `frontend/index.html`): it then runs the same engine in
the browser, on the same streets, with a 24,000-trip real-data sample.

In the control room: drag to rotate, right-drag to pan, scroll to zoom; `W A S D`, `Q E`, `R F`.
Click any ring to see why ORCA chose that car. Press **Watch the tour** for the guided walkthrough.

### Operator controls

Anyone can watch and trigger incidents. Speed, pause, demand level and fleet size change the city
for every viewer, so they are operator-only: set `ORCA_ADMIN_TOKEN` and open `/app?token=…` once,
or run locally (loopback is trusted). `ORCA_OPEN_CONTROLS=1` opens them to everyone, which
`docker-compose.yml` does for local use.

## Real data

| What | Source | Used for |
|---|---|---|
| 12.7M yellow-taxi trips, January 2015 | [NYC TLC trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | 6.2M Manhattan weekday trips replayed; hourly demand and speed profile; demand model; trip-duration validation |
| 63 Manhattan taxi zones | TLC taxi-zone shapefile | forecasting zones, trip placement, the island's shape |
| 939 km of drivable street, 4,454 intersections | [OpenStreetMap](https://www.openstreetmap.org) (ODbL) | routing, car movement, rendering |
| 44,693 building footprints with surveyed heights | [NYC Open Data, BUILDING](https://data.cityofnewyork.us/d/5zhs-2jue) | the 3D skyline |

Coordinates are projected with our own implementation of New York State Plane (EPSG:2263) and
rotated by Manhattan's 29° street grid, the same transform for streets, buildings and trips.

## Train, tune, benchmark

```bash
make train          # demand (real pickups), ETA (telemetry on real streets), real trip-duration model
python -m orca.calibrate   # fit street speeds to real trip times
make tune           # scoring weights by successive halving on seeds 101-109
make bench          # 10 seeds x 2 scenarios + a full day, confidence intervals, significance tests
make test           # routing on both cities, invariants, matching, real data, security, browser e2e
make lint           # ruff, ESLint, TypeScript
make video          # record, narrate (neural voice), score and encode the tour
```

## API

| Method | Path | |
|---|---|---|
| GET | `/` | home page |
| GET | `/app` | control room |
| GET | `/media/{file}` | tour video (MP4, WebM), captions, poster, score |
| GET | `/api/health` | engine status, city, which models are loaded |
| GET | `/api/stream?view=smart\|base` | Server-Sent Events: `frame` at 10 Hz, `summary` at 1 Hz |
| POST | `/api/command` | `{"cmd": "spike" \| "outage" \| "accident"}`; operator: `"speed" \| "pause" \| "demand"` with `"value"` |
| POST | `/api/reset` | operator: `{"fleet": 1200, "seed": 7}` |
| GET | `/api/explain/{request_id}` | why ORCA chose that car, every candidate's score, the request's timeline |
| GET | `/api/analytics?since=60` | wait statistics from the SQLite trip ledger: percentiles, per zone, per hour |
| GET | `/api/trips?limit=50` | most recent pickups |
| GET | `/api/models` | training metrics of the models in use, and the latest benchmark |
| GET | `/metrics` | Prometheus: dispatch metrics and HTTP requests by route and status |

## Engineering

* **CI** on every push: lint, type-check, unit + real-data + browser tests, Docker build and smoke
  test, lock freshness ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
* **Reproducible**: exact dependency locks, fixed seeds, data pipelines as scripts, large files in Git LFS.
* **Secure by default**: operator-only global controls, rate limits, body and stream limits, CSP and
  security headers, non-root container.
* **Deployable**: Docker multi-stage image with health check; [`render.yaml`](render.yaml) and
  [`fly.toml`](fly.toml).

Design notes, the data pipelines and how this would scale out: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Honest limits

* Streets, buildings, demand and hourly speeds are real. Individual car movement along the streets
  and driver behaviour (acceptance, response time) are simulated: no public dataset has live taxi
  GPS traces.
* The TLC publishes taxi zones, not exact points, so each trip is placed at a random point inside its
  real zone. This limits any model's precision (and the speed calibration's per-trip fit).
* Traffic is one citywide speed per hour, not per street; signals and turn delays are folded into it.
* The ETA model learns from simulated pickup legs; the real-trip model validates the approach on real data.
* One process holds the whole state in memory; [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) describes
  the split into services at real scale.
