"""Build a taxi-zone centroid table from the official NYC TLC zone shapefile.

Needed only for trip files that use PULocationID / DOLocationID (2016 onward).
Pure Python: reads the .shp polygons and the .dbf attributes directly.

    # download and unzip https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip
    python scripts/zone_centroids.py data/taxi_zones/taxi_zones.shp --out data/zone_centroids.csv

Output columns: LocationID, x, y, borough, zone (x / y in the shapefile's
projected units, NY State Plane feet; the loader only needs relative positions).
"""
import argparse
import csv
import struct
from pathlib import Path


def read_dbf(path):
    data = Path(path).read_bytes()
    n_rec, hdr_len, rec_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("latin-1")
        flen = data[pos + 16]
        fields.append((name, flen))
        pos += 32
    rows = []
    for i in range(n_rec):
        off = hdr_len + i * rec_len + 1  # skip deletion flag
        row = {}
        for name, flen in fields:
            row[name] = data[off:off + flen].decode("latin-1").strip()
            off += flen
        rows.append(row)
    return rows


def polygon_centroid(points, parts):
    """Area-weighted centroid over all rings (holes have opposite winding and subtract)."""
    A = cx = cy = 0.0
    bounds = list(parts) + [len(points)]
    for k in range(len(parts)):
        ring = points[bounds[k]:bounds[k + 1]]
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
            cr = x0 * y1 - x1 * y0
            A += cr
            cx += (x0 + x1) * cr
            cy += (y0 + y1) * cr
    if abs(A) < 1e-9:
        xs, ys = zip(*points)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return cx / (3 * A), cy / (3 * A)


def read_shp(path):
    data = Path(path).read_bytes()
    pos, shapes = 100, []
    while pos < len(data):
        _rec, length = struct.unpack(">ii", data[pos:pos + 8])
        content = data[pos + 8:pos + 8 + length * 2]
        pos += 8 + length * 2
        stype = struct.unpack("<i", content[:4])[0]
        if stype not in (5, 15, 25):
            shapes.append(None)
            continue
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = struct.unpack(f"<{n_parts}i", content[44:44 + 4 * n_parts])
        o = 44 + 4 * n_parts
        pts = [struct.unpack("<dd", content[o + 16 * i:o + 16 * i + 16]) for i in range(n_points)]
        shapes.append((pts, parts))
    return shapes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shp")
    ap.add_argument("--out", default="data/zone_centroids.csv")
    a = ap.parse_args()
    shapes = read_shp(a.shp)
    attrs = read_dbf(Path(a.shp).with_suffix(".dbf"))
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["LocationID", "x", "y", "borough", "zone"])
        for shp, row in zip(shapes, attrs):
            if shp is None:
                continue
            x, y = polygon_centroid(*shp)
            w.writerow([int(float(row.get("LocationID") or row.get("OBJECTID"))), f"{x:.1f}", f"{y:.1f}",
                        row.get("borough", ""), row.get("zone", "")])
    print(f"Wrote {a.out} with {len(attrs)} zones")


if __name__ == "__main__":
    main()
