# Final Approach: disruption negotiation for arrival waves (Autonomous SG 2026, Airbus Challenge 2)

Design: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` · API contract: `docs/api-contract.md`

**Live demo:** https://disruption-negotiator.onrender.com · **Deck (36 s):** [YouTube](https://youtu.be/JpgnufOeDQQ) · [MP4](deck/final-approach-deck.mp4)

[![Final Approach deck: click to play on YouTube](deck/poster.png)](https://youtu.be/JpgnufOeDQQ)

## 1. Pitch

When weather or a late inbound disrupts an arrival, the airline duty manager coordinates by phone between the tower, the crew and ground handling. This prototype replaces those calls with exactly three agents: ATC, PILOT (for the focus flight) and GROUND. The input is one real disrupted flight, a row of `data/challenge2/flights.csv` (DOT 2015, `WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20`). The wave is built at its destination airport, with the other `flights.csv` arrivals in the same window as context and weather fetched on demand from IEM (cited). Each agent sees only its own domain and can only make moves inside its own authority. A deterministic rule engine decides whether any move or plan is legal; no model ever does. Decision models choose among the options the engine offers and vote. Claude (Haiku 5.5) only narrates the finished outcome for the duty manager and decides nothing; every run from the UI gets a brief, with a deterministic template if Claude is unavailable. In the default mode (formulas, chosen by the benchmark) no model takes any decision; decision models run in `decision` and `decision+claude` modes. The output is a revised arrival (time, runway, gate, action, delay vs schedule) plus the full log, and the human approves or overrides. BTS Jun-Aug 2024 waves are still supported as a second source.

## 2. Live demo and local run

Live: https://disruption-negotiator.onrender.com

Render free tier: it sleeps when idle, so expect a cold start of about 1 minute.

Local:

```bash
pip install -r requirements.txt
uvicorn atm.server:app --port 8000        # API, MCP and (if built) the dashboard at http://localhost:8000
```

Frontend build (served at `/` from `frontend/dist` when present; without it the API still runs):

```bash
cd frontend && npm ci && npm run build
```

Docker (multi-stage: builds the frontend, then the Python API; listens on 7860):

```bash
docker build -t disruption-negotiator .
docker run -p 7860:7860 disruption-negotiator
```

Keys are optional and live in `.env` (gitignored): `OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `ANTHROPIC_API_KEY`. With none set, negotiation runs on the local formulas and narration uses a deterministic template. The Docker image sets `ATM_REQUIRE_REAL=1`.

## 3. Architecture

Input: pick a flight with `GET /api/flights`, id `F2015-<row_id>` (BTS 2024 wave ids still work).

### System

```mermaid
flowchart LR
  subgraph DATA["Inputs"]
    F["flights.csv row<br/>(DOT 2015, the disrupted flight)"]
    WB["challenge2_operational_constraints.xlsx<br/>(workbook limits)"]
    REF["Real reference data<br/>runways (OurAirports), METAR (IEM),<br/>gates (OpenStreetMap)"]
    SIM["Seeded simulation<br/>gate occupancy, fuel, crew, connections"]
  end

  BUILD["Wave builder<br/>flight + other arrivals that hour<br/>at the destination"]
  F --> BUILD
  REF --> BUILD
  SIM --> BUILD

  subgraph NEG["Negotiation (LangGraph, max 3 turns)"]
    direction TB
    ATC["ATC agent<br/>runway, slot, order"]
    PIL["PILOT agent<br/>own flight: speed, ground delay, divert"]
    GND["GROUND agent<br/>gates, remote stand"]
    ATC --> PIL --> GND
  end

  ENGINE["Rule engine (deterministic)<br/>12 rules, cites source and values<br/>lists legal moves, CHECKs every plan"]
  DEC["Decision layer: picks a move / votes<br/>jev-1.14 (default, benchmark winner) or formulas; one model at a time:<br/>jev-1.14 → d1-ultra → elephant-1 → formula<br/>(next only on timeout or invalid answer)"]
  FB["Fallback solver (deterministic)<br/>beam search + safety net<br/>if no consensus after 3 turns"]
  NAR["Claude Haiku 5.5<br/>writes the duty-manager brief<br/>(explains only, decides nothing)"]

  BUILD --> NEG
  WB --> ENGINE
  ENGINE <-->|"legal moves / CHECK"| NEG
  DEC <-->|"which move? accept?"| NEG
  NEG -->|"no consensus"| FB
  FB --> OUT
  NEG -->|"consensus"| OUT
  OUT["Result<br/>revised arrival time, runway, gate or diversion<br/>+ per-turn log + limits used"]
  OUT --> NAR

  subgraph SERVE["Serving"]
    API["FastAPI + SSE<br/>/api/flights, /api/wave, /api/negotiate-wave"]
    UI["React dashboard<br/>timeline, per-turn changes, Approve / Override"]
    MCP["MCP server /mcp/sse (read-only)"]
  end
  OUT --> API --> UI
  NAR --> API
  API --- MCP

  BENCH["Benchmark (50 seeded rows)<br/>arms: formulas, 3 models, status quo,<br/>centralised optimiser; outage test; engine-off ablation"]
  BUILD -.-> BENCH
  NEG -.-> BENCH
```

### Negotiation graph (exported from LangGraph)

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	triage(triage)
	open(open)
	atc(atc)
	pilot(pilot)
	ground(ground)
	decide_turn(decide_turn)
	fallback(fallback)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> triage;
	atc --> pilot;
	decide_turn -. &nbsp;next&nbsp; .-> atc;
	decide_turn -.-> fallback;
	decide_turn -.-> finalize;
	fallback --> finalize;
	ground --> decide_turn;
	open --> atc;
	pilot --> ground;
	triage --> open;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Protocol (`atm/wave/graph.py`, LangGraph). Per turn each agent (ATC, then PILOT, then GROUND) sends ONE `AGENT_TURN` message holding its moves, the moves the engine rejected with their violations, its proposal, its votes and a rationale. The engine runs a `CHECK` after every proposal. Consensus means all 3 agents `ACCEPT` the same plan that passed `CHECK`, within a turn. After at most 3 turns without consensus, a deterministic beam solver with a safety net produces the plan and the reason is logged.

Components (all under `atm/`):

- **Wave builder** (`wave/flights2015.py`, `wave/build.py`): builds the wave from a `flights.csv` row (or a BTS 2024 wave), real runways, METAR, FAA registry and OSM gates, plus seeded simulation of what no public source has. Deterministic per id.
- **Workbook limits** (`wave/constraints.py`): airport capacity, wake table, fuel and crew rules parsed from `data/challenge2/challenge2_operational_constraints.xlsx` (sheet `rules_of_play`), with hard-coded fallbacks equal to the workbook.
- **Rules engine** (`wave/rules.py`): every check is a formula, and every result cites rule, source and input values. Twelve rules:
  - `ATC-RWY-OPEN`: runway exists and is open.
  - `ATC-WAKE-SEP`: workbook wake distance (3-8 nm) between leader and follower on the same physical runway, converted to time at 140 kt (x1.25 when wet or low visibility).
  - `ATC-RWY-CAPACITY`: arrivals per runway per rolling 60 min within the workbook cap.
  - `ATC-TAXI-ROUTE`: taxi route to the gate is usable.
  - `ATC-WIND-LIMITS`: crosswind and tailwind from real METAR against FAA Order 8400.9 section 6d and demonstrated crosswind by type.
  - `PLT-EARLIEST-ETA`: no landing before the flight can physically arrive.
  - `PLT-FUEL-RESERVE`: hold fits within extra fuel above alternate plus 30 min final reserve.
  - `PLT-CREW-DUTY`: in-block (or alternate arrival) within 13 h of crew report time; report = scheduled departure - 60 min. A bust that even the earliest landing cannot avoid is reported, not vetoed.
  - `GND-GATE-SIZE`: gate fits the aircraft.
  - `GND-GATE-FREE`: gate not occupied, including by other wave flights, with the airport turnaround time.
  - `GND-GATE-NOTICE`: gate changes need at least 20 min notice.
  - `GND-CREW-DUTY`: reports ground overtime past the 8 h shift. It never vetoes; overtime is a cost.
- **Moves and authority** (`wave/moves.py`): ATC changes runway, slot and order. PILOT changes only its own flight (speed reduction, ground delay, divert, accept). GROUND changes gates only.
- **Utilities** (`wave/utility.py`): ATC scores the wave, PILOT its own flight, GROUND gates and crew (overtime at 1.5x). Voting uses a reservation schedule that drops each turn.
- **Safety**: every move and every proposal is engine-checked, votes are cast only on `CHECK`-passed plans, the final plan's feasibility is re-checked, and models only choose among offered options.
- **Decision layer** (`decide.py`): per call, a chain of OpenRouter models, then TypeSafe (Jev), then local formulas. Each call has a 2 s timeout and one retry on 429/5xx. A circuit breaker skips an entry for 60 s after 3 consecutive failures. Every fallback is logged and shown in the UI.
- **Fallback solver** (`wave/fallback.py`): beam search over legal moves maximising the worst agent's utility, with a remote-stand, ground-delay or divert safety net. Legality is still decided only by the rules engine. It never raises.
- **Narration** (`wave/narrate.py`): Claude writes the brief from the structured outcome; deterministic template on any failure.
- **API and SSE** (`server.py`): REST under `/api` (`/api/flights`, `/api/waves`, `/api/wave/{id}`, `/api/runs/{id}`, `/api/benchmark`, `/api/config`) and `POST /api/negotiate-wave`, which streams messages as server-sent events. Output is `result.revision` plus the full log. Contract v2.0 is `docs/api-contract.md`.
- **MCP server** (`mcp_server.py`): read-only, at `/mcp/sse`. Six tools: `list_waves`, `get_wave`, `fcfs_plan`, `legal_moves`, `check_plan`, `negotiate` (formulas only).

### Limits applied (`rules.LIMITS`, `rules.limits_for`)

Every change and assumption is returned in `result.limits`. Summary:

| Limit | What we do | Source |
|---|---|---|
| Wake separation | Workbook distances (`min_separation_nm`, 3-8 nm) converted to time at a 140 kt approach speed (3 nm = 77 s, 5 nm = 129 s, 8 nm = 206 s); same physical runway | workbook (nm column); 140 kt is an assumption |
| Wet or low-visibility factor | x1.25 on wake times | assumption |
| B757 | Treated as Heavy | assumption |
| Runways and wind | Real runways and METAR instead of the workbook runway count (used only as the capacity divisor) | real data |
| Runway capacity | `max_runway_ops_per_hour / active_runways` per runway; counts arrivals only | workbook, arrivals only |
| Turnaround, gates, spare gates | Per airport from workbook (turnaround 45/35 min, spare gates); wave models `min(gates, 12)` gates plus a remote stand with 2 positions | workbook; remote stand is an assumption |
| Fuel burn, reserve, spare | 2400 / 6500 kg/h, 30 min final reserve, 45 min spare fuel | workbook |
| Contingency fuel 5% | Not credited: it covers en-route deviations, and the workbook's 45 min spare-on-arrival is what is checked at the destination. Not crediting it is the conservative reading | assumption |
| Flight crew 13 h duty | Enforced by `PLT-CREW-DUTY`; report time = scheduled departure - 60 min | workbook; report offset is an assumption |
| Ground shift | 8 h, overtime at 1.5x is a cost, not illegal | workbook |
| Gate change notice | At least 20 min; remote stand exempt | workbook |
| Speed reduction | Capped at 8 min | assumption |
| Taxi-in time | 6 min near / 10 min far (runway and pier paired by halves), remote stand far plus 10 min bus; no taxiway closures. The data has gate names but no gate positions or taxiway network | assumption |

## 4. Data and provenance

| Layer | Source | Status |
|---|---|---|
| Focus flight, delays, context arrivals | DOT 2015 `flights.csv` (challenge file) | real |
| Second source: flights, delays, queue | BTS On-Time, Jun-Aug 2024, arrivals to 30 hubs | real |
| Runways (ends, headings, length) | OurAirports | real |
| Weather at arrival hour (wind, visibility, ceiling, wet/dry) | Iowa Environmental Mesonet METAR archive, fetched on demand and cited | real |
| Airport capacity, turnaround, wake table, fuel and crew rules | challenge workbook (synthetic planning numbers) | given by the challenge |
| Tail to aircraft type, wake category, gate size | FAA aircraft registry | real (US-registered tails) |
| Gates and wide-body gates | OpenStreetMap, 10 hubs | real |
| Gate occupancy | seeded simulation | simulated, badged |
| Fuel on board | seeded simulation | simulated, badged |
| Crew shifts | seeded simulation, anchored to the flight's actual delayed ETA | simulated, badged |
| Passenger connections | seeded simulation | simulated, badged |
| Two wide gates per hub where OSM has none | simulated | simulated, badged |

Layers missing for a flight fall back to a generic model and are badged in the UI.

## 5. Evaluation

**What the measured impact is:** every final plan obeys every rule (0 violations in every arm), while the uncoordinated status quo breaks at least one rule on 16 of 50 waves; with the rule engine switched off, 32% of the agents' plans are illegal; a negotiation takes about 1 s in formulas mode and 4-8 s with jev-1.14 (`stats.elapsed_ms`). Delay is roughly neutral: on the 34 waves where the status quo is legal, the negotiated plans are about 2 min better in total. Negotiation buys legality first; minutes are a small bonus.

Method (`python3 -m atm.wave.benchmark --n 50 --arms <models>,formula --concurrency 4 --seed 7`):

- 50 seeded `flights.csv` rows, stratified over the 30 busiest hubs (BTS 2024 waves remain available with `--source bts`).
- Selection rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie goes to lower Brier.
- Arms: each candidate decision model (full chain behavior, same waves), the formula arm (no model), and the status quo (FCFS slots, scheduled gates, no coordination).
- Metrics:
  - consensus %: waves that reached agreement within 3 turns; fallback % is the rest, ending in the deterministic solver.
  - error rate: % of decisions answered after the arm's model failed (non-empty `fallback_from`).
  - latency p50/p95: over model-answered decisions only.
  - Brier: mean `(p_accept - y)^2` over model-answered votes, where `y` is the formula verdict (utility at or above reservation). It measures agreement with the formula, not with ground truth.
  - agreement with formula: share of decisions matching the formula arm.
  - outcome means (delay, fuel, passengers protected, crew overtime): over waves that succeeded in every arm.
  - status-quo comparison: the status quo may itself break rules, so regressions against it are reported only on waves where the status quo is feasible.
- Outage test: the primary model is replaced by an invalid id and the chain must still resolve every wave.
- Engine-off ablation: the share of formula-arm final plans that are infeasible under the real rules when the rules engine is disabled.

<!-- BENCHMARK -->
Run: `python3 -m atm.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,openrouter:baseten/elephant-1,formula --concurrency 4 --seed 7 --outage-n 20` (31 Oct 2026, live models, final rules). Same 50 flights.csv rows (seeded, stratified over 30 hubs) for every arm; all arms finished all 50.

| Arm | Consensus (raw-number prompts → judgement-only) | Mean turns | Rule violations | Total delay, all wave arrivals (min) [95% CI] | Gap to optimiser (min) | Pax protected | Brier* | p50 / p95 ms | Errors | Same plan as formulas |
|---|---|---|---|---|---|---|---|---|---|---|
| typesafe/jev-1.14 | 94% → **98%** | 1.32 | 0 | 351.7 [305, 398] | 1.4 | 25.8% | 0.0060 | 480 / 602 | 0% | 74% |
| liquid/d1-ultra | 82% → **96%** | 1.34 | 0 | 351.9 [305, 398] | 1.7 | 25.5% | 0.0016 | 622 / 822 | 0% | 86% |
| baseten/elephant-1 | 68% → **98%** | 1.34 | 0 | 351.9 [305, 398] | 1.6 | 25.8% | 0.0135 | 557 / 647 | 0% | 72% |
| formula | 98% | 1.32 | 0 | 351.7 [305, 398] | 1.4 | 25.5% | n/a | n/a / n/a | 0% | 100% |
| centralised optimiser (beam search, no negotiation) | - | - | 0 infeasible | 350.3 [303, 397] | 0 | 25.8% | - | ~2.3 s/wave | - | - |
| status quo (no negotiation) | - | - | breaks a rule on 16/50 waves | 343.5 | - | 26.5% | - | - | - | - |

\* Brier is measured against the formula verdict (accept if utility >= reservation), not ground truth.

- **Code does the maths, the model judges.** Following TypeSafe's System One guidance ("keep deterministic work in code"), the decision models never see raw utilities. Code pre-computes every comparison into words: per move, whether it fixes or adds rule breaches, whether it is better or worse for the agent, and the engine's rank; per vote, whether the plan meets the agent's minimum this turn, with explicit yes/no criteria. The model only makes the final pick and the accept/reject. The model is offered exactly the moves the formula may take (a Pilot may divert only for its own fuel problem or when every runway is closed).
- **Result: the decision models now match the formulas.** jev-1.14 reaches 98% consensus in 1.32 turns with 351.7 min total delay, 1.4 min from the optimiser (formulas: 351.7, 1.4). With raw numbers in the prompt the same models reached 68-94% consensus and jev-1.14 carried 10 min more delay.
- **Selection: `typesafe/jev-1.14`** by the rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier
- **How good is "good"?** The centralised optimiser (the beam-search solver run on each wave with no negotiation) is the reference for what one coordinator would pick. Three self-interested agents negotiating land within about 1.5 min of it.
- **Status quo:** it breaks at least one rule on 16 of 50 waves. On the 34 waves where it is legal, the negotiated plans are also faster: 337.2 min (formulas) and 337.5 min (jev-1.14) against 339.2 min for the status quo.
- **Safety:** 0 rule violations in any final plan, every arm. **Engine-off ablation:** 32% of the agents' final plans break a rule when the engine is disabled.
- **Outage test:** primary model replaced by a dead endpoint on 20 waves; 20/20 resolved with a legal plan.
- **At scale (1,000 rows, `eval/BENCHMARK_1000.md`):** jev-1.14 reaches 99.5% consensus in 1.20 turns with 329.8 ± 10.6 min mean total delay, against 329.3 ± 10.6 for the formulas, with the same outcome on 100% of the 999 waves.
- Full report: `eval/BENCHMARK.md`, raw data `eval/benchmark.json`.
<!-- /BENCHMARK -->

### Demo replays

Recorded runs in `data/replay/` (live jev-1.14) play back with no model calls. Waves without a recording run live.
- `F2015-69315` ATL, fog: DL2239 on 08R, gate E16, +26 min; agreed in turn 2 after the agents concede.
- `F2015-75` DFW, crosswind runway closures: AA1344 on 13L, gate A38, +119 min; agreed in turn 1.
- `F2015-77162` DEN, closures: DL927 on runway 25, gate C35, +133 min; turn 1.
- `F2015-270287` ORD: OO5559 on 09L, gate B18, +57 min; turn 1.
- `F2015-58926` MIA: UA1634 on runway 12, gate A1, +32 min; agreed in turn 2.

Failure case, live (no recording): `F2015-102056` (DL2019). In turns 1-3 no plan on the table passes the engine CHECK, so the log shows three `NO_CONSENSUS` messages and a `FALLBACK` with its reason; the safety net diverts DL2019 to CSG (about 320 min). The system declines to invent a plan it cannot prove legal.

## 6. What went wrong and what we fixed

- **First real-data run: 24 to 52% consensus.** Simulated crew shifts ignored real delays, so about 70% of flights were infeasible before any agent moved. Crew shifts are now anchored to the actual delayed ETA.
- **VRB wind closed every runway.** It is now treated as calm.
- **No wide gates.** OSM had none at many hubs; we add two simulated wide gates where it has none, badged.
- **Divert delay ignored existing delay.** Fixed.
- Formula consensus rose from 47% to 97% on 30 waves.
- **First benchmark: 47 to 74% model error rate.** Concurrency 16 with a 2 s timeout caused timeouts under load; we reran at concurrency 4.
- **Spec update at 12:45.** The challenge now wants 3 agents and turns on `flights.csv` rows, so we moved from N-pilot waves to 3-agent turns.
- **Final review found two bugs.** Weather delay was dropped from the ETA, and pilots diverted just to clear Ground's gate conflict. Both fixed.
- **First `flights.csv` benchmark picked a model that fell back to the solver on 32% of waves.** We added the consensus >= 90% gate to the selection rule.

## 7. Limits

- The workbook limits are synthetic planning numbers, and we relax or assume several of them (table above).
- Gate occupancy, fuel, crew shifts and connections are simulated. Outcome gains depend partly on those assumptions.
- Brier is measured against the formula verdict, not ground truth.
- n = 50 waves; small differences between arms are not significant.
- The delay utility caps at 60 minutes, so flights that are hours late gain little from negotiation.
- The airborne flag ignores time zones (BTS waves); about 8% of flights are misclassified.
- METAR is the latest observation at or before the wave start (flights.csv rows) or the end-of-hour observation (BTS waves).
- Alternate airports may be small airports.
- p95 model latency is above the 1 s target.
- Utility weights are design judgements, not calibrated to airline cost data. Wake separation is a time-based simplification.
- Render free tier sleeps when idle.

## 8. Repo map and tests

```
atm/             backend
  server.py        FastAPI app, SSE, mounts /mcp and the built frontend
  decide.py        decision layer: model chain, timeout, breaker
  jev.py           Jev (TypeSafe) client
  mcp_server.py    read-only MCP tools
  wave/            build, flights2015 (flights.csv waves), constraints (workbook limits), rules, moves, utility,
                   graph, fallback, baseline, benchmark, narrate, replay
  (negotiation.py, plans.py, scenario.py, baseline.py, evaluate.py: earlier single-flight mode, kept for back-compat)
frontend/        React dashboard (owned by the frontend teammate)
data/            challenge2/ (flights.csv, workbook), waves index, replay/ demo runs
eval/            benchmark outputs (BENCHMARK.md, benchmark.json)
scripts/         data fetch/extract; split_flights2015.py, split_arrivals.py; make_samples.py (API samples)
docs/            design spec, API contract, graph.mmd
tests/           pytest suite
Dockerfile, render.yaml   deployment
```

Tests (121):

```bash
python3 -m pytest -q
```

## License

MIT
