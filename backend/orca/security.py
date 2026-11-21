"""HTTP hardening for the ORCA server: access control, rate limits, size limits, headers, logging.

All of it is pure ASGI middleware, so it never buffers the Server-Sent Events stream.

Access policy
  * Everyone can watch (pages, stream, read-only API) and trigger incidents (spike, outage,
    accident), rate-limited per client.
  * Controls that change the shared city for every viewer (speed, pause, demand level,
    reset / fleet size) are "operator" actions:
      - with ORCA_ADMIN_TOKEN set, they need `Authorization: Bearer <token>`;
      - without it (local development), they are allowed from loopback addresses only.

Environment
  ORCA_ADMIN_TOKEN       operator token (enables protected mode)
  ORCA_OPEN_CONTROLS=1   anyone may use operator controls (local demos, e.g. Docker on your own machine)
  ORCA_TRUST_PROXY=1     take the client address from X-Forwarded-For (behind a reverse proxy)
  ORCA_MAX_STREAMS       concurrent SSE streams per client address (default 6) and
  ORCA_MAX_STREAMS_TOTAL ... in total (default 500)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass

log = logging.getLogger("orca.http")

OPERATOR_COMMANDS = {"speed", "pause", "demand"}
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token buckets keyed by (client, rule). `rate` tokens per second, up to `burst`."""

    def __init__(self, rules: dict[str, tuple[float, float]]):
        self.rules = rules
        self.buckets: dict[tuple[str, str], Bucket] = {}

    def allow(self, client: str, rule: str, now: float | None = None) -> tuple[bool, float]:
        rate, burst = self.rules[rule]
        now = time.monotonic() if now is None else now
        b = self.buckets.get((client, rule))
        if b is None:
            b = self.buckets[(client, rule)] = Bucket(burst, now)
        b.tokens = min(burst, b.tokens + (now - b.updated) * rate)
        b.updated = now
        if b.tokens >= 1:
            b.tokens -= 1
            return True, 0.0
        if len(self.buckets) > 50_000:                     # bound memory under address churn
            self.buckets = {k: v for k, v in self.buckets.items() if now - v.updated < 600}
        return False, (1 - b.tokens) / rate


RULES = {
    "command": (0.5, 8),       # 1 every 2 s sustained, bursts of 8
    "reset": (1 / 30, 2),      # 1 every 30 s
    "explain": (5, 30),
    "api": (20, 60),
}


def client_addr(scope) -> str:
    if os.environ.get("ORCA_TRUST_PROXY") == "1":
        for k, v in scope.get("headers", []):
            if k == b"x-forwarded-for":
                return v.decode().split(",")[0].strip()
    c = scope.get("client")
    return c[0] if c else "unknown"


def header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


def is_operator(scope) -> bool:
    if os.environ.get("ORCA_OPEN_CONTROLS") == "1":
        return True
    token = os.environ.get("ORCA_ADMIN_TOKEN")
    if token:
        auth = header(scope, b"authorization") or ""
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), token.encode())
    return client_addr(scope) in LOOPBACK


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
     b"font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
     b"object-src 'none'; base-uri 'self'; frame-ancestors 'self'"),
]


async def _json(send, status: int, body: dict, extra: list | None = None):
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()), *(extra or [])]})
    await send({"type": "http.response.body", "body": data})


class HardeningMiddleware:
    """Security headers, request ids, access log, body limits, rate limits, operator checks, stream caps."""

    MAX_BODY = 4096

    def __init__(self, app, metrics: dict | None = None):
        self.app = app
        self.limiter = RateLimiter(RULES)
        self.streams: dict[str, int] = defaultdict(int)
        self.max_streams = int(os.environ.get("ORCA_MAX_STREAMS", 6))
        self.max_total = int(os.environ.get("ORCA_MAX_STREAMS_TOTAL", 500))
        self.metrics = metrics if metrics is not None else defaultdict(int)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        client = client_addr(scope)
        rid = header(scope, b"x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapped(msg):
            if msg["type"] == "http.response.start":
                status_holder["status"] = msg["status"]
                msg = dict(msg)
                msg["headers"] = [*msg.get("headers", []), *SECURITY_HEADERS, (b"x-request-id", rid.encode())]
            await send(msg)

        def done():
            st = status_holder["status"]
            self.metrics[(method, _route(path), st // 100)] += 1
            if path != "/api/stream":
                log.info(json.dumps(dict(rid=rid, client=client, method=method, path=path, status=st,
                                         ms=round((time.perf_counter() - t0) * 1000, 1))))

        # ---- limits that apply before the app runs
        rule = ("reset" if path == "/api/reset" else "command" if path == "/api/command"
                else "explain" if path.startswith("/api/explain") else "api" if path.startswith("/api/") and path != "/api/stream" else None)
        if rule:
            ok, retry = self.limiter.allow(client, rule)
            if not ok:
                status_holder["status"] = 429
                await _json(send_wrapped, 429, {"ok": False, "error": "Too many requests, slow down."},
                            [(b"retry-after", str(max(1, round(retry))).encode())])
                return done()

        if method == "POST":
            body = await _read_body(receive, self.MAX_BODY)
            if body is None:
                status_holder["status"] = 413
                await _json(send_wrapped, 413, {"ok": False, "error": "Request body too large."})
                return done()
            if (path == "/api/reset" or (path == "/api/command" and _cmd(body) in OPERATOR_COMMANDS)) and not is_operator(scope):
                status_holder["status"] = 403
                await _json(send_wrapped, 403, {"ok": False, "error": "This control changes the city for every viewer and "
                                                "is reserved for the operator on this server."})
                return done()
            receive = _replay(body)

        if path == "/api/stream":
            if self.streams[client] >= self.max_streams or sum(self.streams.values()) >= self.max_total:
                status_holder["status"] = 429
                await _json(send_wrapped, 429, {"ok": False, "error": "Too many open streams."}, [(b"retry-after", b"5")])
                return done()
            self.streams[client] += 1
            try:
                await self.app(scope, receive, send_wrapped)
            finally:
                self.streams[client] -= 1
                if self.streams[client] <= 0:
                    del self.streams[client]
                done()
            return
        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            done()


def _route(path: str) -> str:
    if path.startswith("/api/explain/"):
        return "/api/explain/{id}"
    if path.startswith("/media/"):
        return "/media/{file}"
    if path.startswith("/assets/"):
        return "/assets/{file}"
    return path if path.count("/") <= 2 else "other"


def _cmd(body: bytes) -> str | None:
    try:
        v = json.loads(body or b"{}")
        return v.get("cmd") if isinstance(v, dict) else None
    except ValueError:
        return None


async def _read_body(receive, limit: int) -> bytes | None:
    chunks, size = [], 0
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            break
        chunk = msg.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not msg.get("more_body"):
            break
    return b"".join(chunks)


def _replay(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}
    return receive
