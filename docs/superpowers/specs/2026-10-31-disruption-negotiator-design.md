# Disruption Negotiator v2: Design

Autonomous SG 2026, Airbus track, Challenge 2. Submission **16:00 today (31 Oct 2026)**, 3-minute pitch.
Builds on the working v1 in this repo (`atm/`, `web/index.html`, `eval/`).

## 1. Intent

When weather or a late inbound disrupts a **wave of arrivals** at a hub, the airline's hub duty
manager today coordinates by phone between the tower (ATC), each flight crew and ground handling.
We replace those calls with independent AI agents that negotiate a joint resolution under
deterministic safety rules, in 3 rounds or fewer, and hand it to the duty manager to approve.

Pitch line: **"Decision models decide, Claude explains, rules guarantee safety, the human approves."**

### Judging rubric this design is built against (docs/airbus_track.pdf)
Technical soundness 25% (right tool per job, LLM/non-LLM split, works beyond a scripted demo) ·
Problem realism 20% · Honest evaluation 20% (baseline, metrics, a visible failure) · Trust and
traceability 20% (every output traces to a rule/signal, uncertainty visible, human decides) ·
Impact 10% · Time discipline 5% (submit early, pitch ≤ 3 min).

### Brief requirements (Challenge 2)
ATC, Pilot and Ground agents, independent (never one prompt playing all roles); structured
messages; explicit constraint checking; traceable consensus log; deterministic non-AI fallback;
convergence/turn limits; never accept a plan that breaks safety margins; negotiate revised arrival
time, gate assignment or flight-plan adjustment in under 3 turns; input = a real DOT row with
`WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20`.

## 2. Goals, non-goals, anti-goals

**Goals**
- Wave mode is the core: 3-5 real disrupted/adjacent flights negotiated jointly.
- Agents build plans themselves through moves within their own authority.
- Real data wherever it exists (flights, arrival queue, runways, weather, aircraft type, gates).
- Decision models chosen by our own benchmark; fallback chain so an API outage never breaks a run.
- Hosted publicly, one URL, judges can open it.

**Non-goals**
- Real ATC procedures in full fidelity (vectoring, approach procedures, departures sequencing).
- Multi-airport network effects beyond the diversion alternate.
- Training our own models.

**Anti-goals (failure even if it "works")**
- Any model (LLM or decision model) deciding legality. Only the rule engine does.
- Hidden simulation: every simulated layer must be badged as simulated.
- Unbounded agent loops; one prompt playing several roles.
- Claims without a test behind them; hiding regressions vs baseline.

## 3. Constraints
- ~4 h of build time from 12:00; two people: **backend owner here (Claude + user steering)**,
  **teammate builds the React frontend on another machine** (taste-skill / impeccable installed there).
- Free hosting only. Keys: `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` in `.env` (gitignored);
  `ANTHROPIC_API_KEY` to be added by the user; `LANGSMITH_API_KEY` later (user will create account).
- Never commit secrets (`.env`, `.env.*`, `*.swp` are gitignored).

## 4. Architecture (decided)

```
frontend/ (Vite + React + TS)  ──/api──▶  FastAPI (atm/server.py)
                                             ├─ LangGraph negotiation graph (atm/graph.py)
                                             │    nodes: OPEN → MOVES(agent i) → CHECK → VOTE → CONSENSUS | CONCEDE → … → FALLBACK
                                             ├─ Domain tool registry (MCP-shaped, per-agent scoping)  ──also served at /mcp
                                             ├─ Decision layer: langchain-typesafe TypeSafeClassifier
                                             │    providers: OpenRouter Decisions | TypeSafe direct | formulas
                                             ├─ Safety engine (deterministic rules)
                                             ├─ Data layer (cached real data + simulated layers)
                                             └─ Claude narration (Anthropic SDK)
```

- **Stack:** Python 3.12, FastAPI, LangGraph, `langchain-typesafe` (alpha 0.0.1a3; falls back to
  `typesafe-sdk` directly if the wrapper misbehaves; verified that `typesafe_sdk.TypeSafeClient(base_url="https://openrouter.ai/api", api_key=OPENROUTER_API_KEY, model=...)`
  reaches OpenRouter decision models), Anthropic SDK for narration.
- **Frontend:** Vite + React + TypeScript in `frontend/`, owned by the teammate. Dev server proxies
  `/api` to uvicorn. `web/index.html` (v1) stays as the fallback dashboard until React is ready.
- **Deploy:** single Docker image (stage 1 Node builds `frontend/dist`; stage 2 Python serves API +
  `dist` at `/`) on a **public Hugging Face Space** (Docker SDK, port 7860). GitHub Action syncs
  `main` to the Space using `HF_TOKEN` repo secret (user sets it). Space secrets hold API keys.
  The live service never fetches datasets or runs the benchmark; it serves committed results.
- **Orchestration: LangGraph** (user's choice). The negotiation is a state graph; LangSmith tracing
  switches on when `LANGSMITH_API_KEY` is present. The graph diagram goes in the pitch.
- **MCP-shaped tools (decided):** every domain action is a typed tool in one registry; each agent
  receives only its own scoped tools (ATC, its own Pilot, Ground, plus read-only safety checks).
  The negotiation calls them in-process. The same registry is exposed as a real MCP server at
  `/mcp` (FastMCP mounted in FastAPI). **Stretch:** an outside agent (e.g. Claude) connects over
  `/mcp` and plays one Pilot live under the same rules.

## 5. Data layer

All fetches are one-off scripts under `scripts/`, results committed as compact extracts under
`data/`. If a layer is missing for a flight, use the generic model and badge it.

| Layer | Source | Coverage | Status |
|---|---|---|---|
| Flights + delay causes, waves, real arrival queue | BTS full monthly On-Time files (transtats PREZIP, no login), **3-6 weather-heavy months** in 2023-2025 | all US airports in those months | real |
| Runways (ends, headings, length) | OurAirports `runways.csv` | all airports in the waves | real |
| Weather at arrival hour (wind dir/speed/gust, vis, ceiling, wx codes → wet/dry) | Iowa Environmental Mesonet ASOS/METAR archive | all wave airports/hours | real |
| Tail → aircraft type → wake category, gate size, hold burn | FAA Releasable Aircraft Database | US-registered tails | real |
| Gate counts, wide-body gates | OpenStreetMap (`aeroway=gate`) | top 50 destination airports | real |
| Gate occupancy, fuel on board, crew shift, pax connections | seeded simulation per flight | all | simulated, badged |

**Wave definition (decided):** all real flights scheduled into one airport in a 60-min window with
at least one disrupted flight (brief's filter). The **3-5 negotiated flights** are the disrupted
ones plus nearest neighbours in the queue; the rest of that hour's real arrivals are a fixed queue.
Which months: builder picks months with the most qualifying waves at the demo hubs (ATL, DFW, DEN,
ORD and other top-30 hubs). The v1 single-flight data (`disruptions.csv`) stays for back-compat.

## 6. Agents and moves (decided)

Agents per wave: **1 ATC**, **1 Pilot per wave flight**, **1 Ground** (5-7 agents). Each sees only
its private view. Plans are built by **moves within the agent's authority**; code lists the legal
moves available from the current plan; the agent picks via its decision model (Choice); the engine
checks after every move and rejects with the rule cited (agent then picks again).

| Agent | Moves |
|---|---|
| ATC | swap two flights in landing order · move a flight to another runway · shift a slot ±2 min |
| Pilot (own flight only) | speed reduction (absorb 3-8 min en route) · ground delay at origin (only if not yet airborne) · request divert · accept current slot |
| Ground | reassign a flight to a free gate · send a flight to the remote stand (bussing) · swap two flights' gates |

**Protocol (graph):** ATC opens with the FCFS plan from the real schedule → each agent in turn
applies up to 2 moves to the table plan, producing a counter-plan → engine checks → all agents vote
on all valid counter-plans (decision model Noul, P(accept) shown) → consensus if a plan is accepted
by **all** agents (pick max-min utility) → else concede (lower reservation) and next round → after
**3 rounds**, deterministic fallback (max-min utility over engine-feasible joint plans from a small
search). Every step is a structured message in the consensus log.

Each agent keeps a utility formula (v1 style, extended to joint plans: Pilots score their own
flight; ATC scores throughput/holding; Ground scores gates/crew/turnaround) used as: the reference
in the benchmark, the formula fallback, and the score terms shown to the decision model.

## 7. Safety engine (deterministic, decided)

Rules apply per flight and **between wave flights**:
- ATC-RWY-OPEN · ATC-WAKE-SEP (by wake category from real aircraft type; weather spacing factor) ·
  ATC-TAXI-ROUTE · **ATC-WIND-LIMITS**: crosswind/tailwind per runway from real METAR, limit = the
  stricter of **FAA Order 8400.9 §6d** (dry: crosswind ≤ 20 kt incl. gusts, tailwind ≤ 5 kt;
  not clear/dry: crosswind ≤ 15 kt, no tailwind beyond calm 0-3 kt) and the **aircraft type's
  demonstrated crosswind** (cited table for ~15 common types; others FAA only).
- PLT-EARLIEST-ETA · PLT-FUEL-RESERVE (hold vs extra fuel above alternate + 30 min final reserve).
- GND-GATE-SIZE · GND-GATE-FREE (incl. between wave flights) · GND-CREW-DUTY.
Each violation message cites the rule, the source (e.g. "FAA Order 8400.9 §6d") and the input
values (e.g. "METAR KDFW 311651Z 21024G33KT").

## 8. Decision layer (decided)

- Question types: Choice (pick a move / plan), Noul (accept, with probability), Score (wave
  severity triage).
- **Providers via one client:** OpenRouter Decisions (any model id) and TypeSafe direct.
- **Fallback chain per call:** benchmark winner → runner-up from a different provider → Jev via
  TypeSafe direct → local formulas. 2 s timeout, one retry on 429/5xx, circuit breaker (3
  consecutive failures → skip provider 60 s). Every fallback is logged and shown in the UI.
- Model per role is configuration (`.env` / dashboard), so the benchmark winner becomes default.

## 9. Claude narration (decided)

Anthropic SDK directly (`ANTHROPIC_API_KEY`, user to add). Claude writes the duty manager brief and
a plain-English transcript from the structured log only; decides nothing; template on failure.

## 10. Evaluation and model benchmark (decided)

- **50 real waves**, same seed for every arm: Jev 1.14, d1-ultra, elephant-1 (all via OpenRouter)
  and the formulas.
- **Baseline:** status quo FCFS on the wave, scheduled gates, no coordination.
- **Metrics:** safety (rule violations in agreed plans, must be 0; ablation with engine off);
  outcome (consensus rate, rounds, delay, holding fuel, connections protected, crew overtime vs
  baseline, fairness vs central optimum); decision quality (agreement with formula reference,
  calibration/Brier of P(accept)); operations (p50/p95 latency, error rate, cost per wave).
- **Outage test:** primary provider deliberately broken mid-run; every wave must still resolve.
- **Selection rule:** best outcome quality with error rate < 2% and p95 latency < 1 s; tie →
  better calibration.
- Outputs `eval/benchmark.json` + `eval/REPORT.md`, served by `/api/eval` and `/api/benchmark`;
  failure cases listed and clickable in the UI.

## 11. Dashboard (frontend contract)

- **Hero visual (decided):** runway + gate timeline (Gantt lanes per runway and gate); wave flights
  move as agents make moves; engine rejections flash red with the rule. **Stretch:** airport map
  with real runway geometry, wind arrow and crosswind per runway.
- Also: wave picker (search/random), mode/model selector, agent message timeline grouped by round
  with decision probabilities, triage card, per-flight real-vs-simulated badges with sources,
  decision panel (final plan per flight, utilities, full rule checklist with evidence, before/after
  vs baseline incl. regressions), Approve / Override (inline note) → `/api/decision`, Claude brief,
  Evaluation + Benchmark tabs.
- **Contract (decided):** backend writes TypeScript types for every response in
  `frontend/src/api/types.ts` plus a saved sample response per endpoint under
  `frontend/src/api/samples/`, so the teammate builds against types before the backend lands.
  Endpoints: existing v1 ones plus `/api/waves`, `/api/wave/{id}`, `POST /api/negotiate-wave`,
  `/api/benchmark`, `/api/models`.

## 12. Schedule (my call, to keep time discipline)
- 12:00-12:30 data fetch scripts running in background; contract types + samples to teammate.
- 12:30-14:00 wave engine: moves, rules incl. wind, LangGraph graph, decision layer + fallbacks.
- 14:00-14:45 benchmark + outage test; pick default model.
- 14:45-15:30 deploy to HF Space, integrate React build, failure-case review.
- 15:30-16:00 pitch rehearsal, submit early. Cut stretch goals first if late.

## 13. Cut order if behind (decided by user)
Cut first → last: (1) outside agent over `/mcp` · (2) airport map · (3) OSM gates → generic gates ·
(4) BTS 3-6 months → 1-2 months · (5) LangSmith tracing · (6) `/mcp` endpoint · (7) LangGraph →
plain Python state machine · (8) React → v1 HTML dashboard.
**Never cut:** wave engine, safety rules incl. FAA wind, decision layer + fallback chain, a
(smaller) benchmark, HF Space deploy. Build in an order where every checkpoint is submittable.

## 14. Calls made after the builder check (my calls, flagged for review)
- **Model availability:** Jev 1.14, d1-ultra and elephant-1 were each called successfully through
  OpenRouter with `typesafe_sdk` (`base_url=https://openrouter.ai/api`), 0.6-0.8 s per call from
  the build machine. The p95 < 1 s selection threshold is borderline; report measured values as
  they are rather than tuning the threshold.
- **Call volume / spend:** one batched request per agent per step (all of an agent's votes in one
  `system_one` call; one Choice per move). ≈50 requests per wave. Run waves concurrently (~16 at a
  time, back off on 429). Estimated total benchmark spend ≈ $1-3; no spend cap needed. If the run
  still exceeds the slot: fewer waves first (50 → 30), never fewer arms.
- **Vote semantics:** the decision model decides: ACCEPT iff P(accept) ≥ 0.5. "Concede" = the
  agent's reservation utility drops each round and is passed to the model as context (plus the
  formula score of the plan). The formula fallback uses the same reservation test. Calibration
  (Brier) is measured against the formula verdict (utility ≥ reservation), stated as such.
- **Public URL:** live negotiations allowed (≈ <1¢ per wave). `POST /api/negotiate-wave` streams
  the log as server-sent events (one event per message, final event = result). Simple per-IP rate
  limit (e.g. 10 waves / 10 min). A set of pre-recorded waves is replayable without model calls
  (`?replay=true`) so the pitch never depends on the network.
- **v1 single-flight mode:** frozen as-is (code and endpoints unchanged, served by
  `web/index.html`). New wave engine lives in new modules; React targets wave endpoints only.

## 15. Left to the builder
Exact utility weights for joint plans; move-list generation details; fallback search method;
LangGraph state schema; file layout inside `atm/`; how many candidate moves to show per Choice
(within model context limits: elephant-1 16k, d1-ultra 32k tokens); React component structure.
