# Shared rides

Real Chicago demand sized for 1,200 cars, served by smaller and smaller fleets; 45 simulated minutes per run, seeds 1-6, paired. 50% of riders accept a shared ride (the same riders for every policy); a car carries up to 3 riders; every pickup stays inside the rider's patience and every shared ride within 1.5x the direct drive plus 120 s.

**With shared rides ORCA keeps the 81 s mean wait of 1,200 solo cars with about 1136 cars: 5% fewer (95% interval 2-10%).** Nearest-car dispatch with all 1,200 cars averages 97 s.

| Cars | Nearest car | ORCA, solo rides | ORCA, shared rides | Riders sharing | Shared ride vs direct | Service km per rider, solo / shared |
|---:|---:|---:|---:|---:|---:|---:|
| 700 | 149s | 111s | 101s (98-104) | 17% | 1.16x | 4.89 / 4.66 |
| 800 | 132s | 99s | 94s (90-98) | 16% | 1.15x | 4.81 / 4.62 |
| 900 | 120s | 94s | 90s (86-93) | 15% | 1.16x | 4.77 / 4.65 |
| 1,000 | 109s | 90s | 86s (83-89) | 13% | 1.15x | 4.74 / 4.63 |
| 1,100 | 102s | 85s | 82s (79-85) | 12% | 1.14x | 4.72 / 4.60 |
| 1,200 | 97s | 81s | 78s (77-81) | 12% | 1.14x | 4.69 / 4.60 |

Mean wait; 95% bootstrap interval over seeds for ORCA with shared rides in brackets. "Shared ride vs direct" is time on board over the direct drive, for riders who rode with someone. Service km count driving to pickups and with riders on board.

## How many riders opt in (1,000 cars)

| Riders accepting a shared ride | Mean wait | Riders sharing | Shared ride vs direct |
|---:|---:|---:|---:|
| 25% | 87s | 4% | 1.13x |
| 50% | 86s | 13% | 1.15x |
| 75% | 82s | 24% | 1.16x |
| 100% | 76s | 37% | 1.16x |
