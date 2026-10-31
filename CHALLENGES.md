# Autonomous SG 2026: challenge notes

Source: shared Drive folder `AUTONOMOUS SG 2026`. Full briefs are in `docs/airbus_track.pdf` and `docs/nus_hackers_track.pdf`.
Large CSVs are not committed. Run `scripts/fetch_data.sh` to download them.

## Airbus track rubric (what we are scored on)

| Criterion | Weight | What wins |
|---|---|---|
| Technical soundness | 25% | Working end to end. **LLMs for reading/explaining; solvers, rules, stats for constraints and scheduling.** An LLM doing legality checks or arithmetic is marked down. |
| Problem realism | 20% | A named user (e.g. a duty manager), what they do today, and what it costs them. |
| Honest evaluation | 20% | A baseline, metrics, and at least one visible failure or refusal, with the reason. |
| Trust and traceability | 20% | Every output traces to a signal or rule. Visible uncertainty. A human stays in control. |
| Impact | 10% | A before/after number against the baseline. |
| Time discipline | 5% | Submit early. Pitch in 3 minutes or less. |

## The five challenges

| # | Problem | Data we have | Core method | Notes |
|---|---|---|---|---|
| 1 | Satellite collision risk triage (Pc tiers: Safe / Elevated / Critical) plus a dashboard | `train_data.csv` (233 MB), `test_data.csv` (24k rows). This is the ESA Kelvins collision avoidance dataset. | Tabular ML (gradient boosting) on `risk = log10(Pc)` | Clean scoring and easy to evaluate honestly. It is a known Kaggle-style dataset, so many teams will build the same thing. |
| 2 | Multi-agent ATC / Pilot / Ground negotiation for disruptions | `airlines.csv` and `airports.csv` only. **`flights.csv` is missing** (see below). | Three separate agents, structured messages, an explicit constraint checker, a consensus log, a deterministic fallback, and agreement in 3 turns or fewer | The best demo of the five. Hard to evaluate with numbers unless we build a baseline. |
| 3 | SAF supply chain in Kalimantan: place and size depots and refineries, route the flows | 80 biomass sites (6,800 kt total) and `airports.dat` (use 4 airports) | MILP facility location (PuLP/OR-Tools) | Small, well-defined problem with one objective number. Light on "AI", heavy on OR. |
| 4 | Traceable AD search with citations, applicability filtering, revisions, and refusals | `ad_nprm.xlsx` (432 FAA AD NPRMs) | Structured parsing + hybrid retrieval + rule-based applicability. The LLM only phrases the answer. | Matches the trust/traceability criterion most closely. The brief asks for 1000+ pages, but 432 spreadsheet rows is less than that. |
| 5 | Forecast airport arrival rate (AAR) and delays >45 min, 3 to 12 h ahead, for ATL and DFW | `Flight_Data_With_Weather_Final.csv` (148k rows, 2023 to 2025, BTS columns + weather) | Time-series features, crosswind/tailwind components (ATL runway 080°, DFW 180°), gradient boosting | AAR is not in the data, so it has to be derived from hourly arrivals. Data leakage across time steps is a known trap. |

## Challenge 2 deep dive (current front-runner)

**Data gap.** The brief says to pick a row with `WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20` from the 2015 DOT data. The Drive folder has only the `airlines.csv` and `airports.csv` lookup tables. The main `flights.csv` (about 5.8M rows, 565 MB) is not there. The datasets link in the PDF (`13h0JZ...`) is not public. Options:
- Kaggle `usdot/flight-delays` contains `flights.csv` (these two lookup files come from the same dataset).
- Ask the organisers for the file.

**Build that scores well under the rubric:**
- Three agents, each with its own prompt, its own private constraints, and its own objective. Never one prompt playing all three roles.
- A typed message schema (e.g. pydantic): `Proposal{slot, runway, gate, eta, fuel_burn_kg, ...}`, `Accept`, `Reject{violated_constraint}`, `CounterProposal`.
- **A deterministic constraint engine** that checks every proposal before an agent can accept it: wake separation minima, minimum fuel reserve, gate free and compatible with the aircraft size, crew duty limits. The LLM proposes, the rules decide. This is the LLM / non-LLM split the rubric asks for.
- A hard limit of 3 rounds. If there is no consensus, a deterministic fallback solver (e.g. first-feasible or a small cost-weighted search) picks the answer and the log records the reason.
- A consensus log: every message, every constraint check, and the final agreement, with timestamps.
- **Evaluation:** run on N sampled disrupted flights, not just one scripted row. Report the consensus rate, the average number of turns, the share of runs where the fallback fired, constraint violations (should be 0), and delay/fuel against a naive "hold until slot" baseline. Show one failure case.
- **User:** the airline duty manager / hub control centre who handles these calls by phone today.
