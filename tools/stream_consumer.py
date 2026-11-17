"""A downstream service reading ORCA's event stream from Redis: live KPIs per dispatcher.

    python tools/stream_consumer.py --redis redis://localhost:6379/0

Joins the consumer group "kpi" on the stream "orca:events" (creating it if needed), reads in blocks,
acknowledges each block after processing, and prints a rolling summary every few seconds: riders
picked up and lost, mean wait over the last 500 pickups, and the share of shared rides. Several copies
can run at once; Redis spreads the events between them. Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import socket
import time
from collections import defaultdict, deque


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--redis", default="redis://localhost:6379/0")
    ap.add_argument("--stream", default="orca:events")
    ap.add_argument("--group", default="kpi")
    ap.add_argument("--every", type=float, default=5.0)
    a = ap.parse_args()
    import redis
    r = redis.Redis.from_url(a.redis, decode_responses=True)
    try:
        r.xgroup_create(a.stream, a.group, id="$", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    me = f"{socket.gethostname()}-{time.time_ns() % 100000}"
    waits = defaultdict(lambda: deque(maxlen=500))
    count = defaultdict(int)
    last = time.time()
    while True:
        for _, entries in r.xreadgroup(a.group, me, {a.stream: ">"}, count=500, block=1000) or []:
            for _id, ev in entries:
                pol = ev.get("policy", "-")
                count[(pol, ev["kind"])] += 1
                if ev["kind"] == "pickup":
                    waits[pol].append(float(ev["wait"]))
                elif ev["kind"] == "dropoff" and ev.get("shared") == "1":
                    count[(pol, "shared")] += 1
            r.xack(a.stream, a.group, *[i for i, _ in entries])
        if time.time() - last >= a.every:
            last = time.time()
            for pol in ("smart", "base"):
                w = waits[pol]
                drops = count[(pol, "dropoff")]
                print(f"{'ORCA' if pol == 'smart' else 'nearest':8s} picked up {count[(pol, 'pickup')]:6d}  lost {count[(pol, 'cancel')]:4d}  "
                      f"mean wait {sum(w) / len(w) if w else 0:6.1f}s  shared {count[(pol, 'shared')] / drops if drops else 0:5.1%}", flush=True)


if __name__ == "__main__":
    main()
