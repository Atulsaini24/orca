# Load test

Viewers streaming the live control room at the same time: packed frames at 10 per second plus a summary every second, 1,200 cars per dispatcher, 20 s per step, one server process and the load generator on the same laptop (so they compete for its cores).
One viewer in fifty also triggers incidents and opens explanations. Produced by `python tools/loadtest.py`.

| Viewers | Connected | Frames per second per viewer (5th percentile) | 95th-percentile gap between frames | Stream bandwidth | Engine advance | Server CPU |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 50 | 9.6 (9.6) | 123 ms | 76 Mbit/s | 2.9 ms | 17% |
| 100 | 100 | 9.2 (9.2) | 128 ms | 158 Mbit/s | 3.5 ms | 22% |
| 200 | 200 | 9.0 (8.9) | 134 ms | 351 Mbit/s | 4.1 ms | 35% |
| 400 | 400 | 8.4 (8.3) | 142 ms | 738 Mbit/s | 4.4 ms | 61% |
| 800 | 800 | 7.0 (7.0) | 192 ms | 1318 Mbit/s | 5.8 ms | 98% |

Each frame and summary is serialized once per engine tick and shared by every viewer, so a viewer costs the server one socket write per message. CPU above 100% means more than one core (the event-loop thread and BLAS / NumPy threads).
