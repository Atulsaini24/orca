"""Small OpenStreetMap Overpass client: one query, several public endpoints, retries with backoff."""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter")
USER_AGENT = "orca-dispatch-research/0.4"


def query(q: str, timeout: int = 300, rounds: int = 3, raw: bool = False):
    """Run an Overpass QL query; returns the parsed JSON (or the raw bytes). Raises after every endpoint failed."""
    errors = []
    for attempt in range(rounds):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read()
                return body if raw else json.loads(body)
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError, ConnectionError) as exc:
                errors.append(f"{url}: {exc}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("Overpass query failed on every endpoint:\n  " + "\n  ".join(errors[-len(ENDPOINTS):]))


def bbox_filter(bbox) -> str:
    s, w, n, e = bbox
    return f"({s},{w},{n},{e})"
