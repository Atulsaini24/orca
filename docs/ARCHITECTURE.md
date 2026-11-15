# Architecture

```
                 ┌────────────────────────── one process today ──────────────────────────┐
 demand source   │                                                                        │
 (NYC TLC replay ┼─▶ World ──requests──▶ NearestDispatcher ─┐                             │
  or synthetic)  │     │ traffic, incidents                  ├─▶ Engine ─▶ Starlette API ─┼─▶ browser
                 │     └────────────────▶ OrcaDispatcher ────┘    snapshots   SSE / REST  │   (WebGL2 + React home)
                 │                          │   ▲                                         │
                 │          GPS pings ──▶ FeatureStore ──▶ ETA / acceptance / demand models│
                 └────────────────────────────────────────────────────────────────────────┘
```

## Real data pipeline

```
NYC TLC yellow_tripdata_2015-01.parquet (12.7M trips, zone IDs)      taxi_zones.shp (official polygons)
                     │                                                          │
                     └──────────────▶ scripts/prepare_tlc.py ◀──────────────────┘
                                         │  Manhattan→Manhattan, ordinary weekdays (17 days, 6.2M trips)
                                         │  each trip placed at a random point inside its real pickup / dropoff zone
          ┌──────────────────────────────┼─────────────────────────────────┐
          ▼                              ▼                                 ▼
 data/nyc_tlc_2015-01_manhattan   artifacts/nyc_traffic_profile.json   frontend/src/tlc_data.js
 .parquet (2M-trip replay)        real median speed + trips by hour     24k-trip sample for the browser engine
```

* **Demand**: the replay folds all days into one typical weekday, keeps the real time-of-day
  and spatial pattern, and scales the rate so the busiest hour is 1.25× what the fleet is built for.
* **Traffic**: the citywide road speed multiplier for each hour is the real median taxi speed
  (trip distance / duration) for that hour divided by the 08:00-20:00 average. Local congestion
  and incidents are layered on top.
* **Geometry**: trips are rotated by Manhattan's 29° street grid and fitted to the simulated square
  city, so the grid is stretched along the island's length.
* The TLC now publishes 2015 with zone IDs rather than GPS points; placing trips uniformly inside
  their zone polygon is the closest honest reconstruction.

## Request lifecycle

1. A request enters both dispatchers at the same moment.
2. **Nearest** offers it at once to the idle car closest in a straight line to its
   last reported position, ignoring how old that position is.
3. **ORCA** holds it until the next 3-second batch, then for each waiting request
   takes the 10 nearest idle cars with fresh GPS, scores every pair and solves
   the batch with `scipy.optimize.linear_sum_assignment`. Above 150 waiting
   requests it switches to a greedy pass so a burst can never stall the loop.
4. The driver answers after 4-11 s. A decline or a 20 s silence sends the
   request back to the queue, excluding that car.
5. Riders cancel after 15 minutes without pickup.

Driver decisions are a deterministic hash of (car, request, attempt), so both
dispatchers face identical drivers.

## Score

```
score = predicted drive time
      + (1 − P(accept)) × 45 s          expected cost of a re-offer
      + zone supply cost                up to 60 s for taking a scarce car from a zone about to get busy
priority: urgent requests ×0.55, minus 0.25 s per second already waited (capped at 300 s)
```

## Models

| Model | Input | Training | Fallback |
|---|---|---|---|
| ETA | distance, arterial share, observed zone speeds at origin / corner / destination, city speed, hour | logged pickup legs under real NYC demand and traffic, time-split; learns a log correction to distance / observed speed so it extrapolates to slower rush-hour traffic | distance over observed zone speed |
| Acceptance | per-driver offers and accepts | online Beta(6.56, 1.44) prior, updated on every answer | the prior |
| Demand | per-zone counts in the last 1-5 min, 15-min mean, city totals, time of day, zone position | real NYC per-minute pickup counts per zone over 17 weekdays, Poisson loss, time-split | EWMA with trend |

Models only see what a real system could observe: GPS pings and request
history, never the simulator's hidden traffic field.

## Scaling this out

At real scale the single process would split along the same seams:

* **Ingest**: GPS pings and requests into Kafka (or Redpanda), partitioned by city zone.
* **Location index**: Redis GEO or H3 cells in memory, replacing the bucket grid.
* **Feature store**: streaming aggregation (Flink) writing zone speeds and demand counts to Redis.
* **Matching**: one stateless matcher per zone cluster, batches solved independently, with a
  boundary pass for requests near zone edges.
* **Models**: served from the matcher process (ETA is called thousands of times a second, so
  network hops would dominate), retrained nightly, promoted only after shadow evaluation.
* **Clients**: the same SSE contract behind a fan-out gateway.

## Frontend

Two pages, both served by the Starlette app:

| Path | What | Built with |
|---|---|---|
| `/` | home page: what ORCA does, live engine status, benchmark and model results, real-data charts, the narrated tour | `web/`: React 18, TypeScript, Vite, Tailwind CSS, Framer Motion. Numbers are imported at build time from `docs/benchmark.json` and `backend/artifacts/*.json` |
| `/app` | the control room | `frontend/`: hand-written WebGL2, bundled by `frontend/build.py` |

The control room is one self-contained file built from five sources:

| File | Role |
|---|---|
| `tlc_data.js` | generated: a 24,000-trip sample of real NYC trips plus real hourly rate and speed |
| `sim.js` | the dispatch engine ported to JavaScript, used when no server is reachable |
| `gl.js` | WebGL2 renderer: instanced meshes (per-instance position, scale, heading, colour), a 2048² sun shadow map with PCF filtering, static shadows cached in their own depth buffer and blitted each frame, screen-space fat lines, water, sky, ACES tone mapping, and an orbit camera with damping, zoom-to-cursor and eased fly-to moves |
| `city.js` | procedural city on the engine's road grid: island and river, central park, lots split per block, podium / shaft / crown towers with height from three density centres, rooftop water towers and plant, street trees, lane markings and crosswalks, and the car and pin meshes |
| `app.js` | live and demo data sources behind one interface, frame interpolation, smooth car turning, spawn and pickup effects, incident shockwaves, the console, the custom cursor and the guided tour |

The tour is a list of chapters, each with a duration, caption, camera move and
an action (pin a decision, trigger an outage, open a tab). A deterministic
capture mode (`index.html?capture`) steps the page frame by frame.
`tools/make_tour_video.py` records it with headless Chromium (Playwright), logs each caption as it
appears, voices one narration line per chapter (neural edge-tts voice when installed, otherwise
Google TTS), synthesises an original score whose sections follow the chapters, ducks the score
under the voice, loudness-normalises to -16 LUFS and writes `docs/orca-tour.mp4` with WebVTT
captions. The same score plays in sync with the in-page tour.
