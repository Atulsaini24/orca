"""Global constants for the ORCA city model and dispatch engine.

The city is a 28 x 28 road grid. One world unit is roughly 30 m, so the
city spans about 8 km per side. Times are in simulated seconds.
"""

GRID = 28                 # intersections per axis
CELL = 10                 # world units between parallel roads
W = (GRID - 1) * CELL     # world width (270 units)
ZONES = 7                 # forecast / rebalancing zones per axis (7 x 7)
ZSIZE = W / ZONES

BASE_SPEED = 0.3          # units per second on a free local road (~32 km/h)
ART_MULT = 1.55           # arterial roads are faster
IDLE_CRUISE = 0.55        # idle units cruise slowly

OFFER_TIMEOUT = 20.0      # an unanswered offer expires after this
STALE_AFTER = 15.0        # GPS older than this is unreliable
ABANDON = 900.0           # a rider cancels after 15 min without pickup
PING_MIN, PING_JITTER = 3.0, 2.0

BATCH = 3.0               # ORCA matches in 3 s batches
REBAL = 20.0              # rebalancing cadence
K = 10                    # candidate units per request
REOFFER = 45.0            # expected cost of a declined / expired offer (s)
HUNGARIAN_MAX = 150       # above this many waiting requests, fall back to greedy
COVER = 0.5               # idle cars wanted per zone, as a share of its forecast 5-min demand

START_CLOCK = 7 * 3600 + 30 * 60   # simulation starts at 07:30
START_DEFAULT = START_CLOCK
DEMAND_PER_UNIT = 0.00118          # requests / s per fleet unit at 1.0x demand

# Scoring weights (see docs/ARCHITECTURE.md, "Score"). Hand-set defaults; `python -m orca.tune`
# searches them on seeds disjoint from the benchmark's and writes artifacts/dispatch_params.json,
# which overrides these values when present.
ZONE_SLOPE = 20.0         # seconds of cost per unit of zone shortfall ratio
ZONE_CAP = 60.0           # maximum zone supply cost (s)
URGENT_W = 0.55           # urgent requests' cost multiplier
WAIT_W = 0.25             # cost credit per second a request has already waited
WAIT_CAP = 300.0          # ... up to this many seconds
DEADLINE_W = 1.0          # weight of the give-up penalty: riders cancel after ABANDON seconds without a car
DEADLINE_COST = 600.0     # cost (s) of a pairing that would arrive after the rider has given up
TUNABLE = ("REOFFER", "COVER", "ZONE_SLOPE", "ZONE_CAP", "URGENT_W", "WAIT_W", "DEADLINE_W", "K")
DEFAULTS = {k: globals()[k] for k in TUNABLE}


def apply(params: dict) -> None:
    """Override tunable weights in this process (used by tuning workers and at start-up)."""
    for k, v in params.items():
        if k not in TUNABLE:
            raise KeyError(f"{k} is not a tunable dispatch parameter")
        globals()[k] = round(v) if k == "K" else float(v)


def load_tuned(city: str = "grid") -> dict:
    """Tuned weights for a city (python -m orca.tune), or {} if none; ORCA_PARAMS=default ignores them."""
    import json
    import os
    from pathlib import Path
    if os.environ.get("ORCA_PARAMS") == "default":
        return {}
    name = "dispatch_params.json" if city == "grid" else f"dispatch_params_{city}.json"
    p = Path(__file__).resolve().parents[1] / "artifacts" / name
    try:
        return json.loads(p.read_text())["params"]
    except (OSError, ValueError, KeyError):
        return {}


TUNED: dict = {}          # set by city.use() for the active city
