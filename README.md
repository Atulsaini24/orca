# ORCA: real-time dispatch engine

ORCA decides which car should take each ride request, live, across a fleet of
over a thousand moving cars, on **real New York City taxi demand** (12.7 million
NYC TLC trip records). It runs head to head against the standard
"send the nearest car" rule on the **same requests, traffic and incidents**, so
every difference in the results comes from the dispatch decisions alone.

Every 3 seconds ORCA scores each (request, car) pair on

* **drive time**, from a gradient-boosted ETA model fed by live zone speeds,
* **acceptance**, a per-driver Beta-Bernoulli estimate learned online from offers,
* **GPS freshness**, so it never offers rides to cars that went silent,
* **zone supply**, from a demand forecaster, so it avoids taking the last spare car out of a zone about to get busy,

then solves the whole batch as a min-cost assignment with the Hungarian
algorithm. Between batches it moves idle cars toward zones forecast to run short.

The control room is drawn by a **hand-written WebGL2 renderer** (no 3D library):
instanced geometry for 3,400 buildings, 1,300 trees and up to 4,000 cars, a
shadow-mapped sun with cached static shadows, screen-space lines and a water
surface. A **100-second guided tour** plays inside the page like a video, with
chapters, captions, narration and an original score, and a narrated recording is in
[`docs/orca-tour.mp4`](docs/orca-tour.mp4). A **home page** (React, TypeScript, Vite,
Tailwind) at `/` explains the product and links into the control room at `/app`.

## Results

Real NYC demand (January 2015 Manhattan weekdays) and real hourly traffic speed,
45 simulated minutes per run, 1,200 cars per dispatcher, two seeds. Full table in
[`docs/benchmark.md`](docs/benchmark.md), reproduce with `make bench`.

| Scenario | Nearest car | ORCA, heuristic models | ORCA, learned models |
|---|---:|---:|---:|
| Steady demand, mean wait | 74 s | 40 s | **39 s** (−47%) |
| Steady demand, p95 wait | 188 s | 100 s | **96 s** |
| Spike + GPS outage + accident, mean wait | 152 s | **88 s** | 90 s (−40%) |
| Same, riders who gave up | 14 | 14 | **10** |

![benchmark](docs/benchmark.png)

Almost all of the gain comes from batch matching, GPS awareness and acceptance
modelling. The learned models add a little on a steady day and are within noise
of the heuristics under stress. Model quality, on held-out data from later in time
than the training data:

| Model | Baseline it replaces | Baseline MAE | ORCA MAE |
|---|---|---:|---:|
| Pickup ETA, gradient boosting on a physics baseline (26k pickup legs) | free-flow distance / speed | 36.7 s | **17.3 s** |
| Pickup ETA | observed zone-speed heuristic | 22.8 s | **17.3 s** |
| Rides per zone, next 5 min, Poisson GBM, trained on real NYC pickups | EWMA with trend | 2.24 | **1.36** |

## Run it

```bash
make install        # pip install -e "backend[dev]" and npm ci in web/
make data           # download 12.7M real NYC TLC trips + taxi zone shapefile, prepare the replay (~1 min)
make web            # build the home page
python -m orca.server --help    # from backend/; or simply:
make run            # http://localhost:8000 (home page), http://localhost:8000/app (control room)
```

Or with Docker, which downloads and prepares the real data during the build:
`docker compose up --build`, then open http://localhost:8000.

The control room also works on its own (open `frontend/index.html`). Without a
server it runs the same engine logic in the browser on a bundled 24,000-trip
sample of the real data, at the real hourly rate, and says so in the header.

In the control room: drag to rotate, right-drag or shift-drag to pan, scroll to
zoom toward the cursor, double-click to fly somewhere, `W A S D` / `Q E` / `R F`
from the keyboard. Click any ring to see why ORCA chose that car, every
candidate's score and the request's timeline. Press **Watch the tour** for the
guided walkthrough.

## Real NYC taxi data

Source: [NYC TLC trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page),
yellow taxi, January 2015, plus the official taxi-zone shapefile.

The TLC now publishes every year with pickup and drop-off **taxi zone IDs** instead
of GPS points. `scripts/prepare_tlc.py`:

* keeps Manhattan-to-Manhattan trips on ordinary weekdays (New Year, MLK Day and the
  26-27 January blizzard removed): **6,193,626 trips over 17 days** across 63 zones;
* places each trip at a uniformly random point inside its real pickup and drop-off
  zone polygons;
* measures the **real median taxi speed for every hour** (trip distance / duration)
  and drives the simulated city's road speed with it;
* writes the 2M-trip server replay, the traffic profile and the browser sample.

The replay folds the days into one typical weekday, keeps the real time-of-day and
spatial pattern, and scales the rate so the busiest hour is 1.25× fleet capacity.
`--demand synthetic` remains available for tests and experiments.

## Train and benchmark

```bash
make train          # demand model on real NYC pickups; ETA model on fleet telemetry under real demand + traffic
make bench          # head-to-head benchmark, writes docs/benchmark.*
make test           # 16 tests: routing, invariants, matching optimality, ledger, timelines, real data, API
```

## The tour video

```bash
pip install -e "backend[video]" && playwright install chromium
make video          # or: python tools/make_tour_video.py [--skip-record] [--tts edge|gtts]
```

Records the guided tour frame by frame in headless Chromium on the real-data
engine, voices one line per chapter from what the captions actually show,
composes an original score whose sections follow the chapters, ducks it under the
voice, normalises to -16 LUFS and writes `docs/orca-tour.mp4` with WebVTT captions.
It uses a neural voice when `edge-tts` is installed (`pip install edge-tts`),
otherwise Google TTS.

## API

| Method | Path | |
|---|---|---|
| GET | `/` | home page (built from `web/`) |
| GET | `/app` | control room |
| GET | `/media/{file}` | tour video, captions, poster and score |
| GET | `/api/health` | engine status and which models are loaded |
| GET | `/api/stream?view=smart\|base` | Server-Sent Events: `frame` at 10 Hz, `summary` at 1 Hz |
| POST | `/api/command` | `{"cmd": "spike" \| "outage" \| "accident" \| "speed" \| "pause" \| "demand", "value": …}` |
| POST | `/api/reset` | `{"fleet": 1200, "seed": 7}` |
| GET | `/api/explain/{request_id}` | why ORCA chose that car, every candidate's score, and the request's timeline |
| GET | `/api/analytics?since=60` | wait statistics per dispatcher from the SQLite trip ledger: percentiles, per zone, per hour |
| GET | `/api/trips?limit=50` | most recent pickups |
| GET | `/api/models` | training metrics of the models in use, and the latest benchmark |
| GET | `/metrics` | Prometheus metrics for both dispatchers |

## Layout

```
backend/orca/
  city.py        road grid, routing, zones
  world.py       clock, traffic (real hourly speed profile), synthetic demand, incidents
  demand.py      NYC TLC trip loading, projection and replay
  features.py    real-time feature store (zone speeds from GPS, demand history)
  models/        ETA, acceptance and demand models with fallbacks
  dispatch.py    fleet, nearest-car baseline, ORCA (Hungarian matching, rebalancing), request timelines
  ledger.py      SQLite trip ledger and analytics queries
  engine.py      both dispatchers on one world; snapshots for clients
  server.py      Starlette API, SSE stream, home page, control room, media, Prometheus metrics
  train.py       model training and evaluation
  bench.py       reproducible benchmark
backend/scripts/ download_tlc.py, prepare_tlc.py (real data), zone_centroids.py
web/             home page: React, TypeScript, Vite, Tailwind CSS, Framer Motion
frontend/src/    control room: tlc_data.js (real-trip sample), sim.js (browser engine), gl.js (WebGL2
                 renderer), city.js (procedural city), app.js (data sources, input, console, tour)
tools/           make_tour_video.py (recording, narration, score, mix)
docs/            architecture notes, benchmark output, tour video, captions and score
```

## Honest limits

* Demand and hourly traffic speed are real. The street grid, individual car
  movement and driver behaviour are simulated, because no public dataset has live
  taxi GPS traces. Manhattan is fitted onto a square city, so it is stretched.
* Trip positions inside a taxi zone are reconstructed (uniform within the real
  polygon), since the TLC no longer publishes coordinates.
* The ETA model learns from the simulator's pickup legs, not from real trips.
* One process holds the whole state in memory. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
  describes how this would split into services at real scale.
