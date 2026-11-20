"""HTTP API and live stream for the ORCA control room.

    python -m orca.server                                   # replays real NYC TLC trips (run `make data` once)
    python -m orca.server --data path/to/other_tlc_file.parquet
    python -m orca.server --demand synthetic                # procedural demand, for tests and experiments

Then open http://localhost:8000 (home page) or http://localhost:8000/app (control room)

Endpoints
  GET  /                    home page (web/dist, built with `npm run build` in web/)
  GET  /app                 control room (frontend/index.html)
  GET  /media/...           tour video, captions and poster (docs/)
  GET  /api/health          engine status and configuration
  GET  /api/stream?view=    Server-Sent Events: `frame` at 10 Hz, `summary` at 1 Hz (&enc=b64: packed car arrays)
  POST /api/command         {"cmd": "speed"|"pause"|"demand"|"spike"|"outage"|"accident"|"logoff", "value": ...}
                            incidents take an optional "x", "z" (world units); logoff: "value" drivers, "minutes"
  POST /api/reset           {"fleet": 1200, "seed": 7, "city": .., "pool": true, "ev": "smart"|"naive"|null}
  GET  /api/explain/{id}    why ORCA assigned request {id} the way it did, with its timeline
  GET  /api/analytics       wait statistics per dispatcher from the trip ledger (?since=minutes)
  GET  /api/trips           most recent pickups (?limit=50)
  GET  /api/models          training metrics of the models in use and the latest benchmark
  GET  /api/events          the domain event stream (?after=<seq>&limit=); also Redis Streams with ORCA_REDIS_URL
  GET  /metrics             Prometheus text format
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .engine import Engine
from .security import HardeningMiddleware

FRONTEND = Path(os.environ.get("ORCA_FRONTEND", Path(__file__).resolve().parents[2] / "frontend" / "index.html"))
ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("ORCA_HOME_DIST", ROOT / "web" / "dist"))
MEDIA = ROOT / "docs"
MEDIA_FILES = {"orca-tour.mp4", "orca-tour.webm", "orca-tour.vtt", "orca-tour-poster.jpg", "orca-tour-score.mp3", "benchmark.png"}
FRAME_HZ, SUMMARY_EVERY = 10, 10   # summary every 10th frame
MAX_FLEET = 10000                  # cars per dispatcher a reset may ask for


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=float)


class Hub:
    """Owns the engine and its clock; caches serialized snapshots so many clients cost little."""

    def __init__(self, opts: dict):
        self.opts = opts
        self.engine = self._new_engine(opts)
        self.tick = 0
        self.cache: dict = {}
        self.explicit_data = bool(opts.get("data"))
        self.lock = asyncio.Lock()
        self.task = None

    def _new_engine(self, opts):
        from . import city as G
        from . import cityspec
        name = opts.get("city") or G.available()[0]
        data, demand = opts.get("data") or cityspec.trips_file(name), opts["demand"]
        if demand == "tlc" and (data is None or not Path(data).exists()):
            demand, data = "synthetic", None                 # this city's real trips are not prepared yet
        e = Engine(seed=opts["seed"], fleet=opts["fleet"], demand=demand, data=str(data) if data else None,
                   zones=opts.get("zones"), db=opts.get("db") or ":memory:", city=name, pool=bool(opts.get("pool")),
                   ev=opts.get("ev"), events=True)
        if demand != opts["demand"]:
            e.world.log("sys", f"No prepared real trips for {e.city.label}: running synthetic demand "
                               f"(python scripts/prepare_trips.py --city {name})")
        e.warm_up(opts.get("warm", 540))
        e.speed = opts.get("speed", 20.0)
        return e

    async def run(self):
        last = time.perf_counter()
        while True:
            now = time.perf_counter()
            async with self.lock:
                self.engine.advance(now - last)
                self.tick += 1
                self.cache.clear()
            last = now
            await asyncio.sleep(1 / 30)

    def frame(self, view, packed=False):
        key = ("f", view, packed, self.tick)
        if key not in self.cache:
            self.cache[key] = dumps(self.engine.frame(view, packed))
        return self.cache[key]

    def summary(self):
        key = ("s", self.tick)
        if key not in self.cache:
            self.cache[key] = dumps(self.engine.summary())
        return self.cache[key]


class CachedStatic(StaticFiles):
    """Static files with a Cache-Control header (ETag and Last-Modified revalidation come from StaticFiles)."""

    def __init__(self, *a, cache: str, **kw):
        super().__init__(*a, **kw)
        self.cache = cache

    def file_response(self, *a, **kw):
        resp = super().file_response(*a, **kw)
        resp.headers["Cache-Control"] = self.cache
        return resp


# HTML must be revalidated on every visit: a cached page points at hashed assets a new build has removed.
NO_CACHE = {"Cache-Control": "no-cache"}


def build_app(opts: dict) -> Starlette:
    hub = Hub(opts)

    @asynccontextmanager
    async def lifespan(app):
        hub.task = asyncio.create_task(hub.run())
        yield
        hub.task.cancel()
        hub.engine.bus.close(timeout=2)

    async def home(request):
        if (HOME / "index.html").exists():
            return FileResponse(HOME / "index.html", headers=NO_CACHE)
        return RedirectResponse("/app")            # home page not built: go straight to the control room

    async def index(request):
        if FRONTEND.exists():
            return FileResponse(FRONTEND, headers=NO_CACHE)
        return PlainTextResponse("frontend/index.html not found", status_code=404)

    async def media(request):
        name = request.path_params["name"]
        if name not in MEDIA_FILES or not (MEDIA / name).exists():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(MEDIA / name, headers={"Cache-Control": "public, max-age=3600"})

    async def health(request):
        e = hub.engine
        return JSONResponse(dict(ok=True, mode="live", t=e.world.t, fleet=e.smart.n, demand=e.demand_label(), pool=e.smart.pool,
                                 city=e.city.name, city_label=e.city.label,
                                 eta_model=e.smart.eta.kind, forecaster=e.smart.forecaster.kind, speed=e.speed))

    async def stream(request: Request):
        view = "base" if request.query_params.get("view") == "base" else "smart"
        packed = request.query_params.get("enc") == "b64"

        async def gen():
            n = 0
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if n % SUMMARY_EVERY == 0:
                    yield f"event: summary\ndata: {hub.summary()}\n\n"
                yield f"event: frame\ndata: {hub.frame(view, packed)}\n\n"
                n += 1
                await asyncio.sleep(1 / FRAME_HZ)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def command(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Send a JSON body like {\"cmd\": \"spike\"}"}, status_code=400)
        async with hub.lock:
            res = hub.engine.command(body)
        return JSONResponse(res, status_code=200 if res["ok"] else 400)

    async def reset(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        opts = dict(hub.opts)
        try:
            opts["fleet"] = int(max(200, min(MAX_FLEET, float(body.get("fleet", opts["fleet"])))))
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "fleet must be a number"}, status_code=400)
        if "pool" in body:
            opts["pool"] = bool(body["pool"])
        if "ev" in body:
            if body["ev"] not in (None, "smart", "naive"):
                return JSONResponse({"ok": False, "error": "ev must be null, \"smart\" or \"naive\""}, status_code=400)
            opts["ev"] = body["ev"]
        opts["seed"] = int(body.get("seed", int(time.time()) % 100000))
        if "city" in body:
            from . import city as G
            if body["city"] not in G.available():
                return JSONResponse({"ok": False, "error": f"Unknown city '{body['city']}'"}, status_code=400)
            opts["city"] = body["city"]
            if not hub.explicit_data:
                opts["data"] = None
        new = await asyncio.to_thread(hub._new_engine, opts)
        async with hub.lock:
            new.speed = hub.engine.speed
            old, hub.engine, hub.opts = hub.engine, new, opts
        old.bus.close(timeout=2)
        return JSONResponse({"ok": True, "fleet": opts["fleet"], "seed": opts["seed"], "city": new.city.name,
                             "pool": bool(opts.get("pool")), "ev": opts.get("ev")})

    async def explain(request: Request):
        try:
            rid = int(request.path_params["rid"])
        except ValueError:
            return JSONResponse({"error": "Request id must be a number"}, status_code=400)
        async with hub.lock:
            return JSONResponse(hub.engine.explain(rid))

    async def analytics(request: Request):
        since = request.query_params.get("since")
        try:
            since = float(since) if since else None
        except ValueError:
            return JSONResponse({"error": "since must be a number of minutes"}, status_code=400)
        async with hub.lock:
            return JSONResponse(hub.engine.ledger.analytics(since, hub.engine.world.t))

    async def trips(request: Request):
        try:
            limit = max(1, min(500, int(request.query_params.get("limit", 50))))
        except ValueError:
            return JSONResponse({"error": "limit must be a whole number"}, status_code=400)
        async with hub.lock:
            return JSONResponse(hub.engine.ledger.recent(limit))

    async def events(request: Request):
        try:
            after = int(request.query_params.get("after", 0))
            limit = max(1, min(2000, int(request.query_params.get("limit", 500))))
        except ValueError:
            return JSONResponse({"error": "after and limit must be whole numbers"}, status_code=400)
        bus = hub.engine.bus
        return JSONResponse(dict(sink=bus.sink, last=bus.seq, events=bus.since(after, limit)))

    async def models(request):
        return JSONResponse(hub.engine.models())

    async def metrics(request):
        lines = [hub.engine.prometheus().rstrip("\n"),
                 "# HELP orca_http_requests_total HTTP requests by method, route and status class.",
                 "# TYPE orca_http_requests_total counter"]
        for (m, route, cls), n in sorted(request.app.state.http_metrics.items()):
            lines.append(f'orca_http_requests_total{{method="{m}",route="{route}",status="{cls}xx"}} {n}')
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    routes = [
        Route("/", home), Route("/app", index), Route("/app/", index), Route("/media/{name}", media),
        Route("/api/health", health), Route("/api/stream", stream),
        Route("/api/command", command, methods=["POST"]), Route("/api/reset", reset, methods=["POST"]),
        Route("/api/explain/{rid}", explain), Route("/api/analytics", analytics), Route("/api/trips", trips),
        Route("/api/models", models), Route("/api/events", events), Route("/metrics", metrics),
    ]
    if (HOME / "assets").is_dir():                         # content-hashed file names: cache for a year
        routes.append(Mount("/assets", CachedStatic(directory=HOME / "assets", cache="public, max-age=31536000, immutable")))
    city_data = FRONTEND.parent / "data"
    if city_data.is_dir():                                 # each city's streets, buildings, trips and config for /app
        routes.append(Mount("/app/data", CachedStatic(directory=city_data, cache="public, max-age=600, must-revalidate")))
    icon = HOME / "favicon.svg"
    routes.append(Route("/favicon.svg", lambda r: FileResponse(icon) if icon.exists() else Response(status_code=204)))
    routes.append(Route("/favicon.ico", lambda r: RedirectResponse("/favicon.svg", status_code=301)))
    if os.environ.get("ORCA_DEV_STATIC"):          # development only: serve extra static files
        routes.append(Mount("/stub", StaticFiles(directory=os.environ["ORCA_DEV_STATIC"])))
    http_metrics: dict = defaultdict(int)
    # gzip shrinks the city data about fourfold; Starlette leaves the event stream uncompressed
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(HardeningMiddleware, metrics=http_metrics),
                                                                  Middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)])
    app.state.hub = hub
    app.state.http_metrics = http_metrics
    return app


def main():
    ap = argparse.ArgumentParser(description="ORCA live dispatch server")
    ap.add_argument("--host", default=os.environ.get("ORCA_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ORCA_PORT", 8000)))
    ap.add_argument("--fleet", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--demand", choices=["synthetic", "tlc"], default=os.environ.get("ORCA_DEMAND", "tlc"))
    ap.add_argument("--data", default=os.environ.get("ORCA_DATA"), help="trip file to replay (default: the city's own)")
    ap.add_argument("--zones", default=None)
    from . import city as G
    ap.add_argument("--city", choices=G.available(), default=os.environ.get("ORCA_CITY"),
                    help="city to run: a built real city (Manhattan first) or the synthetic grid")
    ap.add_argument("--db", default=os.environ.get("ORCA_DB"), help="SQLite file for the trip ledger (default: in memory)")
    a = ap.parse_args()
    if a.demand == "tlc" and a.data and not Path(a.data).exists():
        ap.error(f"trip file not found: {a.data}")
    import uvicorn
    print("Warming up the city…")
    app = build_app(vars(a))
    print(f"ORCA home page:    http://{a.host}:{a.port}")
    print(f"ORCA control room: http://{a.host}:{a.port}/app")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
