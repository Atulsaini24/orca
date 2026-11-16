# Traffic-aware routing

Drivers route by the real speed of every zone at the current hour (measured from the city's own trip records) and around live accidents, like a navigation app. Each route below is timed under the conditions the simulator actually drives in, and compared with the route chosen without that knowledge. Produced by `python -m orca.routing_report`.

| City | Hour | Routes that change | Driving time saved: mean | 90th percentile | Most |
|---|---|---:|---:|---:|---:|
| Manhattan, New York | 08:00 | 22% | 0.3% | 0.9% | 7% |
| Manhattan, New York | 17:00 | 25% | 0.5% | 1.8% | 10% |
| Manhattan, New York | 03:00 | 15% | 0.1% | 0.1% | 4% |
| Chicago, downtown core | 08:00 | 24% | 0.3% | 0.9% | 6% |
| Chicago, downtown core | 17:00 | 22% | 0.3% | 1.1% | 7% |
| Chicago, downtown core | 03:00 | 20% | 0.2% | 0.4% | 5% |

Speeds are known per taxi zone (neighbourhood in Chicago), not per street, so most routes keep their shape and the average gain is small; it is largest across zones that slow down unevenly at rush hour.

## Around an accident (17:00)

| City | Routes through the accident | Time saved: mean | median | 90th percentile |
|---|---:|---:|---:|---:|
| Manhattan, New York | 598 | 7.0% | 1.4% | 22.5% |
| Chicago, downtown core | 466 | 10.4% | 7.9% | 27.0% |
