"""Load test: how many people can watch the live control room at once.

    python tools/loadtest.py                                  # starts its own server, 50 to 800 viewers
    python tools/loadtest.py --url http://host:8000 --viewers 100 400

Each simulated viewer opens the event stream exactly as the control room does (packed frames, 10 per
second, and a summary every second), reads every byte, and records when each frame arrives. A few
viewers also trigger incidents and poll the explain API, like people clicking around. For each step it
reports the frames per second each viewer actually received (target 10), the 95th-percentile gap between
frames, the stream bandwidth, how far the engine fell behind real time (from /metrics), and the server
process's CPU. Writes docs/loadtest.md and docs/loadtest.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


async def viewer(session, base, seconds, stats, clicker=False):
    gaps, n_frames, nbytes, last = [], 0, 0, None
    t_end = time.perf_counter() + seconds
    try:
        async with session.get(f"{base}/api/stream?view=smart&enc=b64", timeout=aiohttp.ClientTimeout(total=seconds + 30)) as r:
            if r.status != 200:
                stats["refused"] += 1
                return
            buf = b""
            async for chunk in r.content.iter_any():
                nbytes += len(chunk)
                buf += chunk
                while b"\n\n" in buf:
                    ev, buf = buf.split(b"\n\n", 1)
                    if ev.startswith(b"event: frame"):
                        now = time.perf_counter()
                        if last is not None:
                            gaps.append(now - last)
                        last, n_frames = now, n_frames + 1
                if clicker and n_frames and n_frames % 50 == 0:
                    await session.post(f"{base}/api/command", json={"cmd": "accident"})
                    await session.get(f"{base}/api/explain/{n_frames}")
                if time.perf_counter() > t_end:
                    break
    except (aiohttp.ClientError, asyncio.TimeoutError):
        stats["errors"] += 1
        return
    stats["fps"].append(n_frames / seconds)
    stats["gaps"].extend(gaps)
    stats["bytes"] += nbytes


async def step(base, n, seconds, pid):
    import psutil
    proc = psutil.Process(pid) if pid else None
    if proc:
        proc.cpu_percent(None)
    stats = dict(fps=[], gaps=[], bytes=0, refused=0, errors=0)
    conn = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conn) as s:
        before = await metric(s, base)
        await asyncio.gather(*(viewer(s, base, seconds, stats, clicker=k < max(1, n // 50)) for k in range(n)))
        after = await metric(s, base)
    cpu = proc.cpu_percent(None) if proc else None
    fps = np.array(stats["fps"]) if stats["fps"] else np.zeros(1)
    gaps = np.array(stats["gaps"]) if stats["gaps"] else np.zeros(1)
    adv = (after[1] - before[1]) / max(1, after[0] - before[0]) if after and before else None
    return dict(viewers=n, connected=len(stats["fps"]), refused=stats["refused"], errors=stats["errors"],
                fps_mean=float(fps.mean()), fps_p5=float(np.percentile(fps, 5)), gap_p95_ms=float(np.percentile(gaps, 95) * 1000),
                mbit_s=stats["bytes"] * 8 / seconds / 1e6, advance_ms_mean=adv, server_cpu=cpu)


async def metric(s, base):
    """(count, sum) of the engine-advance histogram from /metrics."""
    try:
        async with s.get(f"{base}/metrics") as r:
            text = await r.text()
    except aiohttp.ClientError:
        return None
    vals = {line.split()[0]: float(line.split()[1]) for line in text.splitlines() if line.startswith("orca_engine_advance_ms_")}
    return vals.get("orca_engine_advance_ms_count", 0), vals.get("orca_engine_advance_ms_sum", 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=None, help="a running server (default: start one on port 8099)")
    ap.add_argument("--viewers", type=int, nargs="+", default=[50, 100, 200, 400, 800])
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--fleet", type=int, default=1200)
    a = ap.parse_args()
    proc, base = None, a.url
    if base is None:
        env = {**os.environ, "ORCA_MAX_STREAMS": "5000", "ORCA_MAX_STREAMS_TOTAL": "5000", "ORCA_OPEN_CONTROLS": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "orca.server", "--port", "8099", "--fleet", str(a.fleet)],
                                cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:8099"
        import urllib.request
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
    try:
        rows = []
        for n in a.viewers:
            r = asyncio.run(step(base, n, a.seconds, proc.pid if proc else None))
            rows.append(r)
            print(f"{n:5d} viewers: {r['connected']} connected, {r['fps_mean']:.1f} frames/s each (5th pct {r['fps_p5']:.1f}), "
                  f"p95 gap {r['gap_p95_ms']:.0f} ms, {r['mbit_s']:.1f} Mbit/s, engine advance {r['advance_ms_mean'] or 0:.1f} ms, "
                  f"server CPU {r['server_cpu'] if r['server_cpu'] is not None else '-'}%", flush=True)
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
    out = dict(fleet=a.fleet, seconds=a.seconds, local=a.url is None, rows=rows)
    (ROOT / "docs" / "loadtest.json").write_text(json.dumps(out, indent=2))
    md = ["# Load test", "",
          f"Viewers streaming the live control room at the same time: packed frames at 10 per second plus a summary every "
          f"second, {a.fleet:,} cars per dispatcher, {a.seconds} s per step, one server process"
          + (" and the load generator on the same laptop (so they compete for its cores)." if a.url is None else "."),
          "One viewer in fifty also triggers incidents and opens explanations. Produced by `python tools/loadtest.py`.", "",
          "| Viewers | Connected | Frames per second per viewer (5th percentile) | 95th-percentile gap between frames | Stream bandwidth | Engine advance | Server CPU |",
          "|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        cpu = f"{r['server_cpu']:.0f}%" if r["server_cpu"] is not None else "–"
        adv = f"{r['advance_ms_mean']:.1f} ms" if r["advance_ms_mean"] is not None else "–"
        md.append(f"| {r['viewers']:,} | {r['connected']:,} | {r['fps_mean']:.1f} ({r['fps_p5']:.1f}) | {r['gap_p95_ms']:.0f} ms | "
                  f"{r['mbit_s']:.0f} Mbit/s | {adv} | {cpu} |")
    md += ["", "Each frame and summary is serialized once per engine tick and shared by every viewer, so a viewer costs the "
               "server one socket write per message. CPU above 100% means more than one core (the event-loop thread and "
               "BLAS / NumPy threads)."]
    (ROOT / "docs" / "loadtest.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print("Wrote docs/loadtest.md")


if __name__ == "__main__":
    main()
