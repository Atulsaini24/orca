# Scale

Manhattan, real demand sized for each fleet; ORCA and nearest-car run side by side on the same requests, so the engine moves twice the fleet. 600 simulated seconds timed after a warm-up, one process, one thread (Intel64 Family 6 Model 140 Stepping 1, GenuineIntel).

| Cars per dispatcher | Wall time per simulated second | Faster than real time | Previous engine | ORCA batch: requests, mean / p95 latency | Decisions per second | Live frame |
|---:|---:|---:|---:|---:|---:|---:|
| 1,200 | 2.4 ms | 414x | 14.7 ms (6.1x faster now) | 2, 2.8 / 4.6 ms | 729 | 1.6 ms, 37 KB |
| 2,500 | 3.4 ms | 296x | – | 4, 3.1 / 4.6 ms | 1,198 | 3.2 ms, 76 KB |
| 5,000 | 5.7 ms | 174x | 78.4 ms (13.7x faster now) | 7, 4.6 / 6.6 ms | 1,590 | 6.5 ms, 152 KB |
| 10,000 | 11.2 ms | 89x | 139.1 ms (12.4x faster now) | 16, 8.8 / 12.1 ms | 1,839 | 13.4 ms, 302 KB |
| 20,000 | 23.7 ms | 42x | – | 33, 18.6 / 24.3 ms | 1,778 | 28.0 ms, 600 KB |

"Previous engine" is the object-per-car engine this project used before (v0.3), timed the same way on the same machine. A decision is one request assigned; decisions per second divide them by the time spent deciding (candidate search, learned ETA model, acceptance and zone costs, the exact assignment).

## Assignment solver

Random sparse batches, ten candidate cars per request, best of three.

| Requests in the batch | Candidate pairs | Dense Hungarian | Sparse LAPJV |
|---:|---:|---:|---:|
| 100 | 995 | 0.4 ms | 0.7 ms |
| 500 | 4,994 | 11.8 ms | 2.8 ms |
| 1,000 | 9,991 | 42.8 ms | 12.3 ms |
| 2,000 | 19,995 | 162.6 ms | 42.0 ms |
| 5,000 | 49,991 | – | 225.0 ms |

ORCA uses the dense method up to 150 waiting requests and the sparse one above; both are exact.
