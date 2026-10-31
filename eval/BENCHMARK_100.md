# Benchmark (100 waves, seed 7, source flights2015)
Generated 2026-10-31T08:09:52+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (100 of 100).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 339.591 | 2.04 | 26.134 | 1.963 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 341.949 | 32.76 | 25.448 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 100/100 | 99.0 | 1.25 | 1.0 | 0 | 342.892 | 75.6 | 25.258 | 1.2 | 66.0 | 0.0058 | 475.5 | 629.2 | 0.0 |
| formula | 100/100 | 99.0 | 1.25 | 1.0 | 0 | 342.695 | 75.72 | 25.11 | 1.16 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 28 waves: F2015-102056, F2015-105396, F2015-112396, F2015-114965, F2015-124231, F2015-144568, F2015-165543, F2015-188955, F2015-208678, F2015-208692, F2015-210010, F2015-215638, F2015-24573, F2015-246479, F2015-254935, F2015-25867, F2015-261128, F2015-268212, F2015-282726, F2015-298859, F2015-299434, F2015-303427, F2015-303755, F2015-315486, F2015-335957, F2015-343919, F2015-56317, F2015-63104.

- typesafe/jev-1.14 vs status quo on 72 feasible-status-quo waves: delay 336.097 min vs 338.072 min
- formula vs status quo on 72 feasible-status-quo waves: delay 335.736 min vs 338.072 min

Optimiser reference (n=100, 100 feasible): mean delay 341.949 min, 95% CI [307.746, 376.152]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [308.853, 376.931]; gap to optimiser 0.943 min; share of optimiser gain over status quo 93.09% (n=24 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [308.661, 376.729]; gap to optimiser 0.746 min; share of optimiser gain over status quo 108.59% (n=24 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** typesafe/jev-1.14

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 629.2 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/100 waves
- Mean delay: typesafe/jev-1.14 342.892 min vs formula 342.695 min vs status quo 339.591 min.

## Outage test

Primary `invalid/model-does-not-exist` disabled on 0 wave(s): 0 resolved. Fallbacks used: {}

## Ablation: safety engine off

not run

## Failures

- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 346.0 > 344.0 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-108685` (typesafe/jev-1.14): regression vs status quo: delay 474.0 > 471.3 min
- `F2015-132152` (typesafe/jev-1.14): regression vs status quo: delay 357.0 > 349.0 min
- `F2015-108685` (formula): regression vs status quo: delay 474.0 > 471.3 min
- `F2015-132152` (formula): regression vs status quo: delay 357.0 > 349.0 min
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 100 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
