# Shared rides

Real Manhattan demand sized for 1,200 cars, served by smaller and smaller fleets; 45 simulated minutes per run, seeds 1-6, paired. 50% of riders accept a shared ride (the same riders for every policy); a car carries up to 3 riders; every pickup stays inside the rider's patience and every shared ride within 1.5x the direct drive plus 120 s.

**With shared rides ORCA keeps the 108 s mean wait of 1,200 solo cars with about 1083 cars: 10% fewer (95% interval 5-13%).** Nearest-car dispatch with all 1,200 cars averages 136 s.

| Cars | Nearest car | ORCA, solo rides | ORCA, shared rides | Riders sharing | Shared ride vs direct | Service km per rider, solo / shared |
|---:|---:|---:|---:|---:|---:|---:|
| 700 | 296s | 186s | 152s (148-156) | 22% | 1.11x | 5.51 / 5.09 |
| 800 | 224s | 150s | 134s (132-136) | 18% | 1.09x | 5.25 / 5.01 |
| 900 | 181s | 132s | 122s (120-123) | 14% | 1.07x | 5.11 / 4.95 |
| 1,000 | 164s | 119s | 112s (109-115) | 14% | 1.07x | 5.03 / 4.91 |
| 1,100 | 149s | 114s | 107s (105-110) | 13% | 1.06x | 5.01 / 4.89 |
| 1,200 | 136s | 108s | 105s (104-106) | 12% | 1.06x | 4.96 / 4.89 |

Mean wait; 95% bootstrap interval over seeds for ORCA with shared rides in brackets. "Shared ride vs direct" is time on board over the direct drive, for riders who rode with someone. Service km count driving to pickups and with riders on board.

## How many riders opt in (1,000 cars)

| Riders accepting a shared ride | Mean wait | Riders sharing | Shared ride vs direct |
|---:|---:|---:|---:|
| 25% | 118s | 4% | 1.05x |
| 50% | 112s | 14% | 1.07x |
| 75% | 107s | 24% | 1.07x |
| 100% | 103s | 38% | 1.08x |
