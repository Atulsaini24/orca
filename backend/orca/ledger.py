"""Trip ledger: every pickup and every rider who gave up, for both dispatchers, in SQLite.

Writes are buffered and flushed in batches, so the simulation loop never waits
on the disk. Use ``:memory:`` (the default) for a throwaway run or a file path
to keep history across server restarts.
"""
from __future__ import annotations

import sqlite3
import threading

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
  run TEXT, policy TEXT, req_id INTEGER, zone INTEGER, urgent INTEGER,
  requested_at REAL, picked_at REAL, wait REAL, car INTEGER, offers INTEGER
);
CREATE TABLE IF NOT EXISTS lost (run TEXT, policy TEXT, req_id INTEGER, zone INTEGER, urgent INTEGER, requested_at REAL, gave_up_at REAL);
CREATE INDEX IF NOT EXISTS trips_run_policy ON trips(run, policy, picked_at);
CREATE INDEX IF NOT EXISTS lost_run_policy ON lost(run, policy);
"""


class Ledger:
    def __init__(self, path: str = ":memory:", run: str = "run", batch: int = 250):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.run, self.batch = run, batch
        self.buf_trips: list[tuple] = []
        self.buf_lost: list[tuple] = []
        self.lock = threading.Lock()

    def pickup(self, policy: str, r, t: float, wait: float, car: int, offers: int):
        self.buf_trips.append((self.run, policy, r.id, r.zone, int(r.p1), r.t, t, wait, car, offers))
        if len(self.buf_trips) >= self.batch:
            self.flush()

    def lost(self, policy: str, r, t: float):
        self.buf_lost.append((self.run, policy, r.id, r.zone, int(r.p1), r.t, t))
        if len(self.buf_lost) >= self.batch:
            self.flush()

    def flush(self):
        with self.lock:
            if self.buf_trips:
                self.db.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?,?)", self.buf_trips)
                self.buf_trips = []
            if self.buf_lost:
                self.db.executemany("INSERT INTO lost VALUES (?,?,?,?,?,?,?)", self.buf_lost)
                self.buf_lost = []
            self.db.commit()

    def analytics(self, since_minutes: float | None = None, now: float | None = None) -> dict:
        """Wait statistics per dispatcher, overall, per zone and per sim-hour."""
        self.flush()
        cond, args = "run = ?", [self.run]
        if since_minutes is not None and now is not None:
            cond += " AND picked_at >= ?"
            args.append(now - since_minutes * 60)
        out = {}
        with self.lock:
            for pol in ("smart", "base"):
                waits = [w for (w,) in self.db.execute(f"SELECT wait FROM trips WHERE {cond} AND policy = ? ORDER BY wait", (*args, pol))]
                n = len(waits)
                pct = lambda q: waits[min(n - 1, int(q * n))] if n else None
                lost = self.db.execute("SELECT COUNT(*) FROM lost WHERE run = ? AND policy = ?", (self.run, pol)).fetchone()[0]
                zones = {z: (m, c) for z, m, c in self.db.execute(
                    f"SELECT zone, AVG(wait), COUNT(*) FROM trips WHERE {cond} AND policy = ? GROUP BY zone", (*args, pol))}
                hourly = [dict(hour=int(h), mean=m, trips=c) for h, m, c in self.db.execute(
                    f"SELECT CAST((picked_at + ?) / 3600 AS INT) % 24 AS h, AVG(wait), COUNT(*) FROM trips "
                    f"WHERE {cond} AND policy = ? GROUP BY h ORDER BY h", (C.START_CLOCK, *args, pol))]
                urgent = self.db.execute(f"SELECT AVG(wait) FROM trips WHERE {cond} AND policy = ? AND urgent = 1", (*args, pol)).fetchone()[0]
                out[pol] = dict(trips=n, lost=lost, mean=sum(waits) / n if n else None, p50=pct(0.5), p90=pct(0.9), p95=pct(0.95),
                                urgent_mean=urgent, zones=[(zones[z][0] if z in zones else None) for z in range(C.ZONES * C.ZONES)],
                                hourly=hourly)
        return out

    def recent(self, limit: int = 50) -> list[dict]:
        self.flush()
        with self.lock:
            rows = self.db.execute("SELECT policy, req_id, zone, urgent, requested_at, picked_at, wait, car, offers FROM trips "
                                   "WHERE run = ? ORDER BY picked_at DESC LIMIT ?", (self.run, limit)).fetchall()
        keys = ("policy", "req_id", "zone", "urgent", "requested_at", "picked_at", "wait", "car", "offers")
        return [dict(zip(keys, r)) for r in rows]
