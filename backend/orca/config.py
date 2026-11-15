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
DEMAND_PER_UNIT = 0.00118          # requests / s per fleet unit at 1.0x demand
