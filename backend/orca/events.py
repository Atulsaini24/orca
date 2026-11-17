"""Domain event stream: every request, assignment, pickup, drop-off, cancellation, charge and incident.

Events go to an in-memory ring (``GET /api/events?after=<seq>``) and, when ``ORCA_REDIS_URL`` is set, to
a Redis Stream (``XADD orca:events MAXLEN ~ 200000``) that other services consume with consumer groups
(``tools/stream_consumer.py``). Publishing never blocks the simulation: events are queued and written in
batches by a background thread with one pipeline round-trip per batch; if Redis is slow or down the
queue is bounded, overflow is counted and dropped, and errors back off and retry. Counters for
published, dropped and failed events are exported on /metrics.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque

log = logging.getLogger("orca.events")

QUEUE_MAX = 50_000


class EventBus:
    def __init__(self, url: str | None = None, stream: str = "orca:events", maxlen: int = 200_000, batch: int = 500,
                 client=None):
        self.stream, self.maxlen, self.batch = stream, maxlen, batch
        self.recent: deque[dict] = deque(maxlen=5000)
        self.seq = 0
        self.published = self.dropped = self.errors = 0
        self.lock = threading.Lock()
        self.redis = client
        url = url if url is not None else os.environ.get("ORCA_REDIS_URL")
        if self.redis is None and url:
            import redis  # optional dependency (pip install redis)
            self.redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self.q: queue.Queue | None = None
        if self.redis is not None:
            self.q = queue.Queue(QUEUE_MAX)
            self.stop = threading.Event()
            self.thread = threading.Thread(target=self._writer, name="orca-events", daemon=True)
            self.thread.start()

    @property
    def sink(self) -> str:
        return "redis" if self.redis is not None else "memory"

    def emit(self, kind: str, **fields):
        with self.lock:
            self.seq += 1
            ev = {"seq": self.seq, "kind": kind, **fields}
            self.recent.append(ev)
        if self.q is not None:
            try:
                self.q.put_nowait(ev)
            except queue.Full:
                self.dropped += 1

    def since(self, after: int, limit: int = 500) -> list[dict]:
        with self.lock:
            return [e for e in self.recent if e["seq"] > after][:limit]

    def _writer(self):
        backoff = 0.5
        while not self.stop.is_set() or not self.q.empty():
            try:
                first = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < self.batch:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                pipe = self.redis.pipeline(transaction=False)
                for ev in batch:
                    pipe.xadd(self.stream, {k: str(v) for k, v in ev.items()}, maxlen=self.maxlen, approximate=True)
                pipe.execute()
                self.published += len(batch)
                backoff = 0.5
            except Exception as exc:                          # broker down: count, back off, keep the simulation running
                self.errors += len(batch)
                log.warning("event stream write failed (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)
                backoff = min(10.0, backoff * 2)

    def close(self, timeout: float = 5.0):
        if self.q is not None:
            self.stop.set()
            self.thread.join(timeout)

    def metrics(self) -> list[str]:
        return [f'orca_events_total{{sink="{self.sink}",outcome="{k}"}} {v}'
                for k, v in (("emitted", self.seq), ("published", self.published), ("dropped", self.dropped),
                             ("failed", self.errors))]
