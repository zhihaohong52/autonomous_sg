> **SUPERSEDED.** This is the v1 single-flight evaluation (rule agents, one flight at a time). It is not comparable with the current 3-agent wave negotiation; see `eval/BENCHMARK.md` and README section 5.

# Evaluation: 500 real disrupted flights (rule agents)

Data: BTS on-time records 2023-2025 filtered to WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20 (11914 flights). Airport state around each flight is simulated (seeded per flight).

| Metric | Negotiated | Status quo |
|---|---|---|
| delay_min | 12.52 | 41.55 |
| fuel_kg | 108.29 | 142.3 |
| hold_min | 4.35 | 5.22 |
| connections_protected_pct | 90.7 | 64.0 |
| crew_overtime_min | 0.17 | 2.77 |
| next_departure_delay_min | 4.96 | 9.45 |
| diversion_pct | 2.0 | 3.0 |
| crew_duty_breaches | 0 | 1 |

| Process metric | Value |
|---|---|
| consensus_rate_pct | 84.6 |
| fallback_rate_pct | 15.4 |
| mean_rounds | 1.87 |
| agreed_in_round | {1: 163, 2: 239, 3: 21} |
| final_plans_violating_a_rule | 0 |
| engine_rejections_per_flight | 7.59 |
| round1_violations_by_rule | {'GND-GATE-FREE': 2091, 'ATC-WAKE-SEP': 2004, 'PLT-FUEL-RESERVE': 390, 'ATC-RWY-OPEN': 148, 'GND-GATE-SIZE': 99, 'ATC-TAXI-ROUTE': 56} |
| ablation_no_engine_unsafe_agreements_pct | 97.6 |
| ablation_rules | {'ATC-WAKE-SEP': 314, 'GND-GATE-FREE': 311, 'ATC-RWY-OPEN': 65, 'GND-GATE-SIZE': 15, 'ATC-TAXI-ROUTE': 1} |
| fairness_vs_central_optimum | 1.0 |
| mean_latency_s | 0.14 |

## Failure cases

- **OO5594 PSC->SFO** (id 1186): burns more fuel than baseline (56 vs 2 kg)
- **HA251 ITO->HNL** (id 1144): burns more fuel than baseline (140 vs 26 kg)
- **DL324 ATL->PDX** (id 1486): no consensus in 3 rounds -> deterministic fallback; burns more fuel than baseline (1244 vs 294 kg)
- **UA334 EWR->IAD** (id 2028): no consensus in 3 rounds -> deterministic fallback
- **DL1390 ORD->LGA** (id 10332): no consensus in 3 rounds -> deterministic fallback; burns more fuel than baseline (564 vs 47 kg)
- **AA544 DCA->TPA** (id 4744): no consensus in 3 rounds -> deterministic fallback
- **DL2004 LAX->DEN** (id 5054): burns more fuel than baseline (292 vs 9 kg)
- **WN2037 CLE->STL** (id 11173): no consensus in 3 rounds -> deterministic fallback
- **WN1713 DEN->LBB** (id 1688): no consensus in 3 rounds -> deterministic fallback
- **AA1106 DFW->ORD** (id 9528): no consensus in 3 rounds -> deterministic fallback
- **UA1508 EWR->MCO** (id 3078): no consensus in 3 rounds -> deterministic fallback
- **WN887 LAX->SJC** (id 6101): no consensus in 3 rounds -> deterministic fallback
