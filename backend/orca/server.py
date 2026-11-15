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
  GET  /api/stream?view=    Server-Sent Events: `frame` at 10 Hz, `summary` at 1 Hz
  POST /api/command         {"cmd": "speed"|"pause"|"demand"|"spike"|"outage"|"accident", "value": ...}
  POST /api/reset           {"fleet": 1200, "seed": 7}
  GET  /api/explain/{id}    why ORCA assigned request {id} the way it did, with its timeline
  GET  /api/analytics       wait statistics per dispatcher from the trip ledger (?since=minutes)
  GET  /api/trips           most recent pickups (?limit=50)
  GET  /api/models          training metrics of the models in use and the latest benchmark
  GET  /metrics             Prometheus text format
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

from .engine import Engine

REAL_DATA = Path(__file__).resolve().parents[1] / "data" / "nyc_tlc_2015-01_manhattan.parquet"
FRONTEND = Path(os.environ.get("ORCA_FRONTEND", Path(__file__).resolve().parents[2] / "frontend" / "index.html"))
ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("ORCA_HOME_DIST", ROOT / "web" / "dist"))
MEDIA = ROOT / "docs"
MEDIA_FILES = {"orca-tour.mp4", "orca-tour.webm", "orca-tour.vtt", "orca-tour-poster.jpg", "orca-tour-score.mp3", "benchmark.png"}
FRAME_HZ, SUMMARY_EVERY = 10, 10   # summary every 10th frame


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=float)


class Hub:
    """Owns the engine and its clock; caches serialized snapshots so many clients cost little."""

    def __init__(self, opts: dict):
        self.opts = opts
        self.engine = self._new_engine(opts)
        self.tick = 0
        self.cache: dict = {}
        self.lock = asyncio.Lock()
        self.task = None

    def _new_engine(self, opts):
        e = Engine(seed=opts["seed"], fleet=opts["fleet"], demand=opts["demand"], data=opts.get("data"),
                   zones=opts.get("zones"), db=opts.get("db") or ":memory:")
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

    def frame(self, view):
        key = ("f", view, self.tick)
        if key not in self.cache:
            self.cache[key] = dumps(self.engine.frame(view))
        return self.cache[key]

    def summary(self):
        key = ("s", self.tick)
        if key not in self.cache:
            self.cache[key] = dumps(self.engine.summary())
        return self.cache[key]


def build_app(opts: dict) -> Starlette:
    hub = Hub(opts)

    @asynccontextmanager
    async def lifespan(app):
        hub.task = asyncio.create_task(hub.run())
        yield
        hub.task.cancel()

    async def home(request):
        if (HOME / "index.html").exists():
            return FileResponse(HOME / "index.html")
        return RedirectResponse("/app")            # home page not built: go straight to the control room

    async def index(request):
        if FRONTEND.exists():
            return FileResponse(FRONTEND)
        return PlainTextResponse("frontend/index.html not found", status_code=404)

    async def media(request):
        name = request.path_params["name"]
        if name not in MEDIA_FILES or not (MEDIA / name).exists():
            return PlainTextResponse("not found", status_code=404)
        return FileResponse(MEDIA / name, headers={"Cache-Control": "public, max-age=3600"})

    async def health(request):
        e = hub.engine
        return JSONResponse(dict(ok=True, mode="live", t=e.world.t, fleet=len(e.smart.agents), demand=e.demand_label(),
                                 eta_model=e.smart.eta.kind, forecaster=e.smart.forecaster.kind, speed=e.speed))

    async def stream(request: Request):
        view = "base" if request.query_params.get("view") == "base" else "smart"

        async def gen():
            n = 0
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if n % SUMMARY_EVERY == 0:
                    yield f"event: summary\ndata: {hub.summary()}\n\n"
                yield f"event: frame\ndata: {hub.frame(view)}\n\n"
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
        opts["fleet"] = int(max(200, min(4000, body.get("fleet", opts["fleet"]))))
        opts["seed"] = int(body.get("seed", int(time.time()) % 100000))
        new = await asyncio.to_thread(hub._new_engine, opts)
        async with hub.lock:
            new.speed = hub.engine.speed
            hub.engine, hub.opts = new, opts
        return JSONResponse({"ok": True, "fleet": opts["fleet"], "seed": opts["seed"]})

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
        Route("/api/models", models), Route("/metrics", metrics),
    ]
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles
    if (HOME / "assets").is_dir():
        routes.append(Mount("/assets", StaticFiles(directory=HOME / "assets")))
    if (HOME / "favicon.svg").exists():
        routes.append(Route("/favicon.svg", lambda r: FileResponse(HOME / "favicon.svg")))
    if os.environ.get("ORCA_DEV_STATIC"):          # development only: serve extra static files
        from starlette.routing import Mount
        from starlette.staticfiles import StaticFiles
        routes.append(Mount("/stub", StaticFiles(directory=os.environ["ORCA_DEV_STATIC"])))
    from collections import defaultdict

    from starlette.middleware import Middleware

    from .security import HardeningMiddleware
    http_metrics: dict = defaultdict(int)
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(HardeningMiddleware, metrics=http_metrics)])
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
    ap.add_argument("--data", default=os.environ.get("ORCA_DATA") or str(REAL_DATA))
    ap.add_argument("--zones", default=None)
    ap.add_argument("--db", default=os.environ.get("ORCA_DB"), help="SQLite file for the trip ledger (default: in memory)")
    a = ap.parse_args()
    if a.demand == "tlc" and not Path(a.data).exists():
        ap.error(f"real trip data not found at {a.data}. Download and prepare it once with `make data` "
                 "(python scripts/download_tlc.py && python scripts/prepare_tlc.py), "
                 "or run with --demand synthetic.")
    import uvicorn
    print("Warming up the city…")
    app = build_app(vars(a))
    print(f"ORCA home page:    http://{a.host}:{a.port}")
    print(f"ORCA control room: http://{a.host}:{a.port}/app")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
