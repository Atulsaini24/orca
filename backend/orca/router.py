"""One process per city behind one port: a supervising reverse proxy.

    python -m orca.router                      # every built city, http://127.0.0.1:8000
    python -m orca.router --cities manhattan chicago --port 8080

Each city runs in its own ``orca.server`` process on a private loopback port, so cities never share
process state (``city.use`` activates one city per process) and one city's failure cannot stop another.

Routing
  /cities/<city>/...   that city's process, prefix removed (/cities/chicago/api/stream -> /api/stream)
  everything else      the default city's process (home page, control room, assets, /api/...)
  /api/cities          the router itself: every city, its port, pid, restarts and live health

Supervision
  A city process that exits is restarted with exponential backoff (1 s up to 60 s). One whose
  /api/health keeps failing (it returns 503 once the simulation clock stops) for STALL_RESTART seconds
  is killed and restarted. Every restart is logged and counted in /api/cities.

Security
  The router sets X-Forwarded-For to the address it received the request from (or, with
  ORCA_TRUST_PROXY=1, the address an upstream proxy reported), and city processes run with
  ORCA_TRUST_PROXY=1 on loopback only. A visitor therefore keeps their own rate limits, and only a real
  local client counts as a loopback operator; a client cannot spoof the header through the router.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse

from .security import client_addr

log = logging.getLogger("orca.router")
ROOT = Path(__file__).resolve().parents[1]
HOP = {b"connection", b"keep-alive", b"proxy-connection", b"transfer-encoding", b"te", b"trailer", b"upgrade",
       b"proxy-authorization", b"proxy-authenticate", b"content-length", b"host", b"x-forwarded-for"}
MAX_BODY = 1 << 20
STALL_RESTART = 30.0          # seconds of failing health checks before a city process is restarted
CHECK_EVERY = 5.0


@dataclass
class City:
    name: str
    port: int
    args: list = field(default_factory=list)
    proc: subprocess.Popen | None = None
    started: float = 0.0
    restarts: int = 0
    ready: bool = False
    failing_since: float | None = None
    last_health: dict | None = None
    backoff: float = 1.0

    def start(self):
        env = {**os.environ, "ORCA_TRUST_PROXY": "1", "ORCA_LOCK_CITY": "1", "ORCA_EXIT_WITH_PARENT": "1",
               "PYTHONUNBUFFERED": "1"}
        db = env.get("ORCA_DB", "")
        if db and "://" not in db:                    # a SQLite file: one per city, never shared by processes
            p = Path(db)
            env["ORCA_DB"] = str(p.with_name(f"{p.stem}_{self.name}{p.suffix or '.db'}"))
        cmd = [sys.executable, "-m", "orca.server", "--host", "127.0.0.1", "--port", str(self.port),
               "--city", self.name, *self.args]
        # stdin is a pipe the child watches: when this process dies, the pipe closes and the child exits
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE)
        self.started, self.ready, self.failing_since = time.monotonic(), False, None
        log.warning("city %s: started pid %d on port %d", self.name, self.proc.pid, self.port)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------------------------------------- proxy
async def _read_request_body(receive) -> bytes | None:
    body = b""
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            return None
        body += msg.get("body", b"")
        if len(body) > MAX_BODY:
            return b"\0" * (MAX_BODY + 1)
        if not msg.get("more_body"):
            return body


async def _pump_chunked(reader, send):
    while True:
        line = await reader.readline()
        if not line:
            return
        size = int(line.split(b";")[0].strip() or b"0", 16)
        if size == 0:
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            return
        data = await reader.readexactly(size)
        await reader.readexactly(2)
        await send({"type": "http.response.body", "body": data, "more_body": True})


async def proxy(scope, receive, send, port: int, path: str, mark: bytes = b""):
    body = await _read_request_body(receive)
    if body is None:
        return
    if len(body) > MAX_BODY:
        await _error(send, 413, "Request body too large")
        return
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
    except (OSError, asyncio.TimeoutError):
        await _error(send, 503, "This city is starting, try again in a moment.")
        return
    qs = scope.get("query_string", b"")
    target = path.encode() + (b"?" + qs if qs else b"")
    head = [scope["method"].encode() + b" " + target + b" HTTP/1.1", b"host: 127.0.0.1:" + str(port).encode(),
            b"connection: close", b"x-forwarded-for: " + client_addr(scope).encode(),
            b"content-length: " + str(len(body)).encode()]
    head += [k + b": " + v for k, v in scope.get("headers", []) if k.lower() not in HOP]
    writer.write(b"\r\n".join(head) + b"\r\n\r\n" + body)

    async def watch_disconnect():
        while (await receive())["type"] != "http.disconnect":
            pass

    watcher = asyncio.create_task(watch_disconnect())
    try:
        await writer.drain()
        status_line = await reader.readline()
        parts = status_line.split(b" ", 2)
        if len(parts) < 2:
            await _error(send, 502, "The city process gave no answer.")
            return
        headers, chunked = [], False
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.rstrip(b"\r\n").partition(b":")
            k, v = k.strip().lower(), v.strip()
            if k == b"transfer-encoding" and b"chunked" in v.lower():
                chunked = True
            if k not in HOP or k == b"content-length":
                headers.append((k, v))
        if mark:                                      # tells the control room which cities have their own process
            headers.append((b"x-orca-router", mark))
        await send({"type": "http.response.start", "status": int(parts[1]), "headers": headers})
        pump = asyncio.create_task(_pump_chunked(reader, send) if chunked else _pump_raw(reader, send))
        done, _ = await asyncio.wait({pump, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if pump in done:
            pump.result()
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            pump.cancel()                             # the viewer left: close the upstream stream too
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        watcher.cancel()
        writer.close()


async def _pump_raw(reader, send):
    while True:
        data = await reader.read(65536)
        if not data:
            return
        await send({"type": "http.response.body", "body": data, "more_body": True})


async def _error(send, status, msg):
    body = json.dumps({"ok": False, "error": msg}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"retry-after", b"5")]})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------------------------- supervisor
async def _health(port: int) -> tuple[int, dict | None]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
    except (OSError, asyncio.TimeoutError):
        return 0, None
    try:
        writer.write(b"GET /api/health HTTP/1.1\r\nhost: 127.0.0.1\r\nconnection: close\r\n\r\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 3)
        head, _, body = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        if b"chunked" in head.lower():                # a single chunk: size line, data
            body = body.split(b"\r\n", 1)[1].rsplit(b"\r\n0", 1)[0]
        return status, json.loads(body or b"null")
    except Exception:
        return 0, None
    finally:
        writer.close()


async def supervise(cities: list[City]):
    while True:
        await asyncio.sleep(CHECK_EVERY)
        now = time.monotonic()
        for c in cities:
            if c.proc is None or c.proc.poll() is not None:
                code = c.proc.returncode if c.proc else None
                if now - c.started >= c.backoff:
                    log.error("city %s: process exited (code %s); restarting", c.name, code)
                    c.restarts += 1
                    c.backoff = min(60.0, c.backoff * 2)
                    c.start()
                continue
            status, h = await _health(c.port)
            c.last_health = h
            if status == 200:
                c.ready, c.failing_since, c.backoff = True, None, 1.0
                continue
            if not c.ready and now - c.started < 300:   # still warming up (models, city data)
                continue
            c.failing_since = c.failing_since or now
            if now - c.failing_since >= STALL_RESTART:
                log.error("city %s: health failing for %.0f s (%s); restarting", c.name, now - c.failing_since,
                          (h or {}).get("error", f"HTTP {status}"))
                c.stop()
                c.restarts += 1
                c.start()


def build_router(cities: list[City]) -> Starlette:
    by_name = {c.name: c for c in cities}
    default = cities[0]
    mark = " ".join(by_name).encode()

    @asynccontextmanager
    async def lifespan(app):
        for c in cities:
            c.start()
        task = asyncio.create_task(supervise(cities))
        yield
        task.cancel()
        for c in cities:
            c.stop()

    async def city_list(request):
        return JSONResponse(dict(default=default.name, cities=[
            dict(name=c.name, port=c.port, pid=c.proc.pid if c.proc else None, ready=c.ready, restarts=c.restarts,
                 alive=bool(c.proc and c.proc.poll() is None),
                 uptime=round(time.monotonic() - c.started, 1) if c.proc else 0,
                 health=c.last_health) for c in cities]))

    app = Starlette(lifespan=lifespan)

    async def asgi(scope, receive, send):
        if scope["type"] == "lifespan":
            await app(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        path = scope["path"]
        if path == "/api/cities":
            await (await city_list(None))(scope, receive, send)
            return
        target, rest = default, path
        if path.startswith("/cities/"):
            name, _, tail = path[len("/cities/"):].partition("/")
            if name not in by_name:
                await _error(send, 404, f"No city '{name}' here; see /api/cities")
                return
            target, rest = by_name[name], "/" + tail
        await proxy(scope, receive, send, target.port, rest, mark)

    asgi.state = app.state                            # TestClient and tooling look for app.state
    return asgi


def main():
    from . import city as G
    ap = argparse.ArgumentParser(description="Run every city in its own process behind one port")
    ap.add_argument("--host", default=os.environ.get("ORCA_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ORCA_PORT", 8000)))
    ap.add_argument("--cities", nargs="+", default=None,
                    help="cities to run, the first is the default (default: ORCA_CITIES or every built city)")
    ap.add_argument("--first-port", type=int, default=int(os.environ.get("ORCA_FIRST_PORT", 8101)))
    ap.add_argument("server_args", nargs=argparse.REMAINDER, help="after --, passed to every orca.server")
    a = ap.parse_args()
    names = a.cities or (os.environ.get("ORCA_CITIES", "").split() or [c for c in G.available() if c != "grid"])
    extra = a.server_args[1:] if a.server_args[:1] == ["--"] else a.server_args
    cities = [City(n, a.first_port + i, extra) for i, n in enumerate(names)]
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    import uvicorn
    print(f"ORCA router: http://{a.host}:{a.port}  cities: " + ", ".join(f"{c.name} (:{c.port})" for c in cities), flush=True)
    uvicorn.run(build_router(cities), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
