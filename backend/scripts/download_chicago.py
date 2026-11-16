"""Download Chicago's real data for ORCA's second city (City of Chicago Data Portal + OpenStreetMap).

    python scripts/download_chicago.py                  # everything missing, September 2026
    python scripts/download_chicago.py --only trips     # roads | buildings | trips

Region: Chicago's busiest core, seven community areas: the Loop, Near North Side, Near South Side,
Near West Side, West Town, Lincoln Park and Lake View.

Sources
  roads      OpenStreetMap drivable ways in the region's bounding box (Overpass API)
  buildings  City of Chicago "Building Footprints" (syp8-uezg), with storeys
  trips      City of Chicago "Transportation Network Providers - Trips (2025-)" (6dvr-xwnh):
             every Uber / Lyft / Via trip starting and ending in the region, paged one day at a time
  zones      neighbourhood and community-area boundaries are fetched separately (see README)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "chicago"
REGION = json.loads((OUT / "region.json").read_text()) if (OUT / "region.json").exists() else None
UA = {"User-Agent": "orca-dispatch-research/0.4"}
API = "https://data.cityofchicago.org/resource"
OSM_CLASSES = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|motorway_link|trunk_link|"
               "primary_link|secondary_link|tertiary_link|living_street")


def fetch(url: str, data: bytes | None = None, attempts: int = 6, timeout: int = 600) -> bytes:
    for k in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # network errors, 429 / 504 from busy servers
            wait = min(120, 5 * 2 ** k)
            print(f"  attempt {k + 1} failed ({exc}); retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"giving up on {url[:120]}")


def roads():
    s, w, n, e = REGION["bbox"]
    m = 0.004                                                     # ~400 m margin around the region
    q = (f'[out:json][timeout:300];way["highway"~"^({OSM_CLASSES})$"]["access"!~"^(private|no)$"]["area"!="yes"]'
         f"({s - m},{w - m},{n + m},{e + m});(._;>;);out body qt;")
    for host in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            body = fetch(host, urllib.parse.urlencode({"data": q}).encode(), attempts=4)
            if body.lstrip().startswith(b"{"):
                dest = ROOT / "data" / "osm" / "chicago_roads.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
                print(f"roads: {dest} ({len(body) / 1e6:.1f} MB)")
                return
        except SystemExit:
            continue
    raise SystemExit("no Overpass server answered")


def buildings():
    s, w, n, e = REGION["bbox"]
    where = f"within_box(the_geom, {n}, {w}, {s}, {e})"
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"$select": "the_geom,stories,year_built,bldg_name1", "$where": where,
                                    "$limit": "50000", "$offset": str(offset), "$order": ":id"})
        page = json.loads(fetch(f"{API}/syp8-uezg.json?{q}"))
        rows += page
        print(f"  buildings: {len(rows):,}", flush=True)
        if len(page) < 50000:
            break
        offset += 50000
    (OUT / "buildings.json").write_text(json.dumps(rows))
    print(f"buildings: {len(rows):,} footprints")


def trips(year: int, month: int):
    """One file per day (finished days are skipped on a re-run), then merged into one CSV."""
    cas = ",".join(f"'{c}'" for c in REGION["cas"])
    cols = ["trip_start_timestamp", "trip_end_timestamp", "trip_seconds", "trip_miles", "pickup_community_area",
            "dropoff_community_area", "pickup_centroid_latitude", "pickup_centroid_longitude",
            "dropoff_centroid_latitude", "dropoff_centroid_longitude"]
    days_dir = OUT / "tnp_days"
    days_dir.mkdir(parents=True, exist_ok=True)
    d, end = date(year, month, 1), date(year + month // 12, month % 12 + 1, 1)
    while d < end:
        part = days_dir / f"{d}.csv"
        if not part.exists():
            where = (f"trip_start_timestamp >= '{d}T00:00:00' AND trip_start_timestamp < '{d + timedelta(days=1)}T00:00:00' "
                     f"AND pickup_community_area in ({cas}) AND dropoff_community_area in ({cas})")
            q = urllib.parse.urlencode({"$select": ",".join(cols), "$where": where, "$limit": "400000"})
            body = fetch(f"{API}/6dvr-xwnh.csv?{q}", timeout=240)
            tmp = part.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(part)
            rows = body.count(b"\n") - 1
            print(f"  {d}: {rows:,} trips", flush=True)
        d += timedelta(days=1)
    dest = OUT / f"tnp_trips_{year}-{month:02d}.csv"
    total = 0
    with dest.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for part in sorted(days_dir.glob(f"{year}-{month:02d}-*.csv")):
            rd = csv.reader(io.StringIO(part.read_text(encoding="utf-8")))
            next(rd, None)
            for row in rd:
                w.writerow(row)
                total += 1
    print(f"trips: {dest} ({total:,} trips)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["roads", "buildings", "trips"])
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--month", type=int, default=9)
    a = ap.parse_args()
    if REGION is None:
        sys.exit("data/chicago/region.json missing: build it from the community areas first")
    jobs = {"roads": roads, "buildings": buildings, "trips": lambda: trips(a.year, a.month)}
    for name, job in jobs.items():
        if a.only in (None, name):
            print(f"== {name}", flush=True)
            job()


if __name__ == "__main__":
    main()
