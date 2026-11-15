"""Download real NYC TLC trip records (run this on your own machine).

    python scripts/download_tlc.py                      # yellow taxi, January 2015
    python scripts/download_tlc.py --year 2015 --month 6
    python scripts/download_tlc.py --zones              # official taxi zone shapefile (needed by prepare_tlc.py)

Source: NYC Taxi & Limousine Commission trip record data,
https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page

Files are Parquet (pyarrow). The TLC now publishes every year with pickup and
dropoff taxi zone IDs rather than coordinates, so also fetch the zone shapefile
and run scripts/prepare_tlc.py, which places trips inside the real zone polygons.
"""
import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="yellow", choices=["yellow", "green", "fhvhv"])
    ap.add_argument("--year", type=int, default=2015)
    ap.add_argument("--month", type=int, default=1)
    ap.add_argument("--out", default="data")
    ap.add_argument("--zones", action="store_true", help="download the taxi zone shapefile instead")
    a = ap.parse_args()
    if a.zones:
        dest = Path(a.out) / "taxi_zones.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve("https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip", dest)
        with zipfile.ZipFile(dest) as z:
            z.extractall(Path(a.out) / "taxi_zones")
        print(f"Saved {Path(a.out) / 'taxi_zones'}")
        return
    name = f"{a.kind}_tripdata_{a.year}-{a.month:02d}.parquet"
    dest = Path(a.out) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{name}"
    print(f"Downloading {url}")

    def hook(n, size, total):
        if total > 0:
            sys.stdout.write(f"\r  {min(100, n * size * 100 // total):3d}%")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest, hook)
    print(f"\nSaved {dest} ({dest.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
