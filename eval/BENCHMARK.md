# Benchmark (50 waves, seed 7, source flights2015)
Generated 2026-10-31T07:22:22+00:00

Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm (50 of 50).

## Arms vs status quo

| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min | agree w/ formula % | Brier | p50 ms | p95 ms | error % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status quo | - | - | - | - | - | 343.478 | 0.72 | 26.538 | 1.92 | - | - | - | - | - |
| Optimiser (centralised beam search, no negotiation) | - | - | - | - | 0 | 350.266 | 64.24 | 25.844 | - | - | - | - | - | - |
| typesafe/jev-1.14 | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 361.992 | 163.84 | 24.568 | 1.36 | 62.0 | 0.0055 | 529.0 | 669.6 | 0.0 |
| liquid/d1-ultra | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 362.238 | 167.28 | 24.272 | 1.36 | 80.0 | 0.0015 | 648.0 | 915.9 | 0.0 |
| baseten/elephant-1 | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 362.198 | 180.88 | 24.568 | 1.36 | 70.0 | 0.0108 | 601.5 | 722.3 | 0.0 |
| formula | 50/50 | 98.0 | 1.32 | 2.0 | 0 | 351.678 | 108.08 | 25.502 | 1.28 | 100.0 | n/a | n/a | n/a | 0.0 |

Status quo infeasible on 16 waves: F2015-102056, F2015-105396, F2015-112396, F2015-124231, F2015-144568, F2015-165543, F2015-208678, F2015-208692, F2015-210010, F2015-246479, F2015-254935, F2015-282726, F2015-298859, F2015-335957, F2015-343919, F2015-63104.

- typesafe/jev-1.14 vs status quo on 34 feasible-status-quo waves: delay 337.5 min vs 339.224 min
- liquid/d1-ultra vs status quo on 34 feasible-status-quo waves: delay 337.441 min vs 339.224 min
- baseten/elephant-1 vs status quo on 34 feasible-status-quo waves: delay 337.5 min vs 339.224 min
- formula vs status quo on 34 feasible-status-quo waves: delay 337.206 min vs 339.224 min

Optimiser reference (n=50, 50 feasible): mean delay 350.266 min, 95% CI [303.445, 397.087]; no models, no negotiation.
- typesafe/jev-1.14: delay 95% CI [312.676, 411.308]; gap to optimiser 11.726 min; share of optimiser gain over status quo 93.81% (n=10 waves with feasible status quo beaten by the optimiser).
- liquid/d1-ultra: delay 95% CI [312.949, 411.527]; gap to optimiser 11.972 min; share of optimiser gain over status quo 93.81% (n=10 waves with feasible status quo beaten by the optimiser).
- baseten/elephant-1: delay 95% CI [312.931, 411.465]; gap to optimiser 11.932 min; share of optimiser gain over status quo 93.81% (n=10 waves with feasible status quo beaten by the optimiser).
- formula: delay 95% CI [305.221, 398.135]; gap to optimiser 1.412 min; share of optimiser gain over status quo 106.19% (n=10 waves with feasible status quo beaten by the optimiser).

Status-quo diversions (mean): 0.0

## Selection

**Winner:** formula

Rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier

- typesafe/jev-1.14 is worse than formula on delay (361.992 > 351.678 min) and not better on pax protected: formula selected.
- typesafe/jev-1.14 meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms.
- Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.
- cost_usd_per_wave = 0.0: output tokens free; input cost not measured.
- fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions answered after this arm failed (a failed wave counts as one error decision).
- typesafe/jev-1.14 p95 all decisions incl. fallbacks: 669.6 ms
- typesafe/jev-1.14 outcomes partly from solver fallback on 1/50 waves
- liquid/d1-ultra p95 all decisions incl. fallbacks: 915.9 ms
- liquid/d1-ultra outcomes partly from solver fallback on 1/50 waves
- baseten/elephant-1 p95 all decisions incl. fallbacks: 722.3 ms
- baseten/elephant-1 outcomes partly from solver fallback on 1/50 waves
- Mean delay: typesafe/jev-1.14 361.992 min vs formula 351.678 min vs status quo 343.478 min.
- Outage test ran with the best eligible model arm typesafe/jev-1.14 (overall winner is formula).

## Outage test

Primary `invalid/model-does-not-exist` disabled on 20 wave(s): 20 resolved. Fallbacks used: {"typesafe/jev-1.14-20261014": 159}

## Ablation: safety engine off

32.0% of formula-arm final plans are infeasible under the real rules when the engine is disabled.

## Failures

- `F2015-302254` (typesafe/jev-1.14): regression vs status quo: delay 346.0 > 344.0 min
- `F2015-102056` (typesafe/jev-1.14): no consensus: deterministic solver fallback
- `F2015-102056` (liquid/d1-ultra): no consensus: deterministic solver fallback
- `F2015-302254` (baseten/elephant-1): regression vs status quo: delay 346.0 > 344.0 min
- `F2015-102056` (baseten/elephant-1): no consensus: deterministic solver fallback
- `F2015-102056` (formula): no consensus: deterministic solver fallback

## Limits

- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.
- Brier is against the formula verdict (utility >= reservation), not human judgement.
- n = 50 waves: differences between arms are indicative, not statistically significant.
- Cost is not measured (0.0).
