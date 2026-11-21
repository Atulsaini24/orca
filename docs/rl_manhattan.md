# Learned repositioning

ORCA's idle cars repositioned three ways on real Manhattan demand, 45 simulated minutes from three times of day, seeds 1-10 (never used in training), paired; everything else identical.

* **Rules**: the hand-written rebalancer: forecast demand per zone against idle cars, nearest surplus zone first.
* **Learned**: a value network (zone embedding + live supply and forecast demand, 2 x 64 ReLU) trained by fitted TD learning over 4 rounds of policy iteration on seeds 201+ (49,552 transitions); cars move to the neighbouring zone whose value beats their own by more than the cost of driving there, with the same budget.
* **None**: idle cars only cruise nearby.

| Start | Rules | Learned | None | Share of the rules' gain the learned policy keeps | Learned vs rules (Wilcoxon p) |
|---|---:|---:|---:|---:|---:|
| morning (07.5 h) | 106.0s | 115.6s (114-117) | 121.7s | 39% | 0.002 |
| evening (17.0 h) | 137.7s | 149.3s (145-154) | 205.2s | 83% | 0.002 |
| night (22.0 h) | 96.3s | 97.5s (95-100) | 139.7s | 97% | 0.375 |

Mean wait for a car; 95% bootstrap interval over seeds for the learned policy in brackets.

## What this shows

Repositioning matters: without it waits are 15-50% longer. The learned value policy recovers much of that, most at night and in the evening peak, but it does not beat the hand-written rules, which aim directly at riders' waits (forecast demand against idle cars) while the learned value is a car's expected rides. Two variants were tried and dropped: discounting the drive to the new zone (a repositioning car stays dispatchable, so the drive is not lost time; with it the policy almost never moved cars), and rewarding short pickups instead of rides (pickups are short wherever idle cars already gather, so the value pointed the wrong way). The rules stay the default; the learned policy runs with `rebalance="learned"`.
