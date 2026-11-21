"""Trip ledger: every pickup and every rider who gave up, for both dispatchers, in SQL.

One code path (SQLAlchemy Core) for SQLite and PostgreSQL:

    Ledger(":memory:")                                        # throwaway run (the default)
    Ledger("backend/ledger/orca.db")                          # a SQLite file kept across restarts
    Ledger("postgresql+psycopg://orca:orca@localhost/orca")   # PostgreSQL (pip install "psycopg[binary]")

Writes are buffered and inserted in batches (one multi-row INSERT per table), so the simulation loop
touches the database once every few hundred trips. Aggregations that differ between SQL dialects (hour of
day) are done in NumPy on the selected rows, so both databases give identical answers.
"""
from __future__ import annotations

import threading

import numpy as np
from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.pool import StaticPool

from . import city as G
from . import config as C

meta = MetaData()
trips = Table("trips", meta, Column("run", String(80)), Column("policy", String(16)), Column("req_id", Integer),
              Column("zone", Integer), Column("urgent", Integer), Column("requested_at", Float), Column("picked_at", Float),
              Column("wait", Float), Column("car", Integer), Column("offers", Integer),
              Index("trips_run_policy", "run", "policy", "picked_at"))
lost = Table("lost", meta, Column("run", String(80)), Column("policy", String(16)), Column("req_id", Integer),
             Column("zone", Integer), Column("urgent", Integer), Column("requested_at", Float), Column("gave_up_at", Float),
             Index("lost_run_policy", "run", "policy"))
TRIP_COLS = [c.name for c in trips.columns]
LOST_COLS = [c.name for c in lost.columns]


def engine_for(path: str):
    if "://" in path:
        return create_engine(path, pool_pre_ping=True)
    if path == ":memory:":
        return create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    return create_engine(f"sqlite+pysqlite:///{path}", connect_args={"check_same_thread": False})


class Ledger:
    def __init__(self, path: str = ":memory:", run: str = "run", batch: int = 250):
        self.db = engine_for(path)
        meta.create_all(self.db)
        self.dialect = self.db.dialect.name
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
            bt, bl = self.buf_trips, self.buf_lost
            self.buf_trips, self.buf_lost = [], []
            if not bt and not bl:
                return
            with self.db.begin() as cx:
                if bt:
                    cx.execute(insert(trips), [dict(zip(TRIP_COLS, row)) for row in bt])
                if bl:
                    cx.execute(insert(lost), [dict(zip(LOST_COLS, row)) for row in bl])

    def _rows(self, cx, policy: str, since: float | None):
        q = select(trips.c.wait, trips.c.zone, trips.c.urgent, trips.c.picked_at).where(trips.c.run == self.run,
                                                                                         trips.c.policy == policy)
        if since is not None:
            q = q.where(trips.c.picked_at >= since)
        rows = cx.execute(q).fetchall()
        return np.array(rows, dtype=float).reshape(-1, 4)

    def analytics(self, since_minutes: float | None = None, now: float | None = None) -> dict:
        """Wait statistics per dispatcher, overall, per zone and per sim-hour."""
        self.flush()
        since = now - since_minutes * 60 if since_minutes is not None and now is not None else None
        out = {}
        nz = G.n_zones()
        with self.db.connect() as cx:
            for pol in ("smart", "base"):
                a = self._rows(cx, pol, since)
                w = np.sort(a[:, 0])
                n = len(w)
                pct = lambda q, w=w, n=n: float(w[min(n - 1, int(q * n))]) if n else None
                n_lost = len(cx.execute(select(lost.c.req_id).where(lost.c.run == self.run, lost.c.policy == pol)).fetchall())
                zone = a[:, 1].astype(int)
                zsum, zcnt = np.bincount(zone, a[:, 0], nz), np.bincount(zone, minlength=nz)
                hour = ((a[:, 3] + C.START_CLOCK) // 3600).astype(int) % 24
                hsum, hcnt = np.bincount(hour, a[:, 0], 24), np.bincount(hour, minlength=24)
                urgent = a[a[:, 2] == 1, 0]
                out[pol] = dict(trips=n, lost=n_lost, mean=float(w.mean()) if n else None, p50=pct(0.5), p90=pct(0.9),
                                p95=pct(0.95), urgent_mean=float(urgent.mean()) if len(urgent) else None,
                                zones=[float(zsum[z] / zcnt[z]) if zcnt[z] else None for z in range(nz)],
                                hourly=[dict(hour=h, mean=float(hsum[h] / hcnt[h]), trips=int(hcnt[h])) for h in range(24) if hcnt[h]])
        return out

    def recent(self, limit: int = 50) -> list[dict]:
        self.flush()
        cols = ("policy", "req_id", "zone", "urgent", "requested_at", "picked_at", "wait", "car", "offers")
        q = select(*(trips.c[k] for k in cols)).where(trips.c.run == self.run).order_by(trips.c.picked_at.desc()).limit(limit)
        with self.db.connect() as cx:
            return [dict(zip(cols, r)) for r in cx.execute(q).fetchall()]
