# Disruption Negotiator v2 (wave mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Execution here:** sonata-loop style: each task = code agent → fresh review agent → fix agent (if review fails) → re-review, before the task counts as done. Tasks in the same **Phase** are independent and are dispatched in parallel (superpowers:dispatching-parallel-agents).

**Goal:** Negotiate a real arrival wave (3-5 flights) between ATC, per-flight Pilot and Ground agents under deterministic safety rules, served live to a React dashboard from a public HF Space, with a model benchmark.

**Architecture:** New package `atm/wave/` (data, rules, moves, utility, graph) on shared dataclasses in `atm/wave/model.py`; decision calls through `atm/decide.py` (fallback chain + circuit breaker); LangGraph drives the protocol and emits contract-shaped messages; FastAPI streams them as SSE. v1 (`atm/negotiation.py` etc.) is frozen and untouched.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, langchain-typesafe (fallback: typesafe-sdk 0.7.5), anthropic, pandas, pytest; Vite + React + TS (teammate); Docker on HF Spaces.

**Spec:** `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` · **Contract:** `docs/api-contract.md` (field names in this plan = contract field names).

## Global Constraints

- Deadline 16:00 31 Oct 2026; cut order per spec §13 if behind (outside-agent MCP → map → OSM gates → months → LangSmith → /mcp → LangGraph → React).
- No model decides legality; only `atm/wave/rules.py`. Every rejection cites rule, source and input values.
- Wind limits: FAA Order 8400.9 §6d — dry: crosswind ≤ 20 kt incl. gusts, tailwind ≤ 5 kt; not clear/dry: crosswind ≤ 15 kt, tailwind 0 (calm 0-3 kt counts as none); stricter of FAA and aircraft-type demonstrated crosswind.
- Max 3 rounds; consensus = accepted by all agents; then deterministic fallback.
- Vote: ACCEPT iff P(accept) ≥ 0.5; formulas: ACCEPT iff utility ≥ reservation.
- Decision call: 2 s timeout, 1 retry on 429/5xx, breaker opens after 3 consecutive failures for 60 s; every fallback logged as a `MODEL_FALLBACK` message.
- Months fixed: BTS 2024-06, 2024-07, 2024-08. Airports: the 30 hubs in `atm/wave/hubs.py:HUBS` (Task 1).
- Never print/commit `.env`; agents never commit (the controller commits after review).
- v1 files (`atm/negotiation.py`, `atm/plans.py`, `atm/scenario.py`, `atm/jev.py`, `web/index.html`) are not modified.
- Tests live in `tests/`, run with `python -m pytest -q`.

## Review Focus

1. Wave with an unknown aircraft type / tail → generic wake LARGE, narrow gate, FAA-only wind limit, provenance `generic`; must not crash.
2. METAR missing or `wind_dir` VRB/null → crosswind = full wind speed (worst case) on every runway, provenance notes it.
3. All runways out of wind limits → every land plan infeasible → fallback picks divert/ground-delay plan, never an illegal one.
4. All decision providers down → run completes on formulas with `MODEL_FALLBACK` messages, outcome still valid.
5. Decision model returns a choice id not in the options / malformed answer → treated as provider failure, next in chain.

(Each has a named test in its owning task.)

---

## File map

| File | Responsibility | Task |
|---|---|---|
| `atm/wave/model.py` | dataclasses for Wave, WaveFlight, Gate, Runway, Metar, Assignment, JointPlan, Move, RuleCheck, Message; `to_dict()` = contract JSON | 1 |
| `atm/wave/hubs.py` | `HUBS` list, `MONTHS` | 1 |
| `tests/fixtures.py` | `make_wave()` synthetic 4-flight wave for unit tests | 1 |
| `scripts/fetch_bts.py` → `data/waves/arrivals.csv.gz`, `data/waves/index.csv` | real flights + wave index | 2 |
| `scripts/fetch_ref.py` → `data/ref/runways.csv`, `metar.csv`, `aircraft.csv`, `gates.csv` | reference data | 3 |
| `atm/wave/wind.py` | wind components + limits + type table | 4 |
| `atm/decide.py` | provider chain, breaker, choice/noul/score | 5 |
| `Dockerfile`, `.github/workflows/deploy-space.yml`, `requirements.txt`, `README` HF header | deploy | 6 |
| `atm/wave/build.py` | `list_waves`, `build_wave` from data | 7 |
| `atm/wave/rules.py`, `atm/wave/moves.py`, `atm/wave/utility.py` | engine, legal moves, metrics/utility | 8 |
| `atm/wave/baseline.py`, `atm/wave/fallback.py` | status quo + max-min search | 9 |
| `atm/wave/graph.py`, `atm/wave/narrate.py` | LangGraph protocol, Claude brief | 10 |
| `atm/server.py` (add routes), `atm/wave/replay.py`, `frontend/src/api/types.ts`, `frontend/src/api/samples/*.json` | API + SSE + contract artefacts | 11 |
| `atm/wave/benchmark.py` → `eval/benchmark.json`, `eval/BENCHMARK.md` | benchmark, outage, ablation | 12 |
| `atm/mcp_server.py` | MCP tool registry at `/mcp` | 13 (cut #6) |

## Phases (parallel groups)

- **Phase 0 (controller, serial):** Task 1.
- **Phase 1 (parallel):** Tasks 2, 3, 4, 5, 6.
- **Phase 2 (parallel):** Tasks 7, 8, 9 (8 and 9 use `tests/fixtures.py`, not real data).
- **Phase 3 (parallel):** Tasks 10, 11 (11 starts against a stub `negotiate_wave` that yields fixture messages, swaps in Task 10 when merged).
- **Phase 4 (parallel):** Tasks 12, 13, deploy run, README/pitch.

---

### Task 1: Shared model, hubs, fixtures, deps (controller)

**Files:** Create `atm/wave/__init__.py`, `atm/wave/model.py`, `atm/wave/hubs.py`, `tests/__init__.py`, `tests/fixtures.py`, `tests/test_model.py`, `requirements.txt`.

**Produces:**
- Dataclasses with exactly the contract's fields (snake_case): `Runway`, `Metar`, `RunwayWind`, `Gate`, `QueueArrival`, `WaveFlight`, `Airport`, `Wave` (= contract `WaveScenario`), `Assignment`, `FlightMetrics`, `JointPlan`, `Move`, `RuleCheck`, `Provenance`. Each has `to_dict() -> dict`.
- `JointPlan.id` = `sha1` of sorted assignment tuples, 10 hex chars. `JointPlan.with_assignment(a: Assignment) -> JointPlan`.
- `hubs.HUBS: list[str]` (30 IATA codes: ATL DFW DEN ORD LAX JFK LAS MCO CLT PHX SEA MIA SFO IAH EWR BOS MSP DTW FLL LGA PHL BWI SLC DCA SAN IAD TPA MDW BNA AUS), `hubs.MONTHS = ["2024-06","2024-07","2024-08"]`.
- `fixtures.make_wave(**overrides) -> Wave`: ATL-like airport, runways 08L/26R (heading 94/274) and 09R/27L, METAR `27015KT` dry, 4 flights (1 HEAVY B772 airborne, 3 LARGE A321/B738, one not airborne), 6 gates (2 wide) + `RS` remote, 3 queue arrivals.
- `requirements.txt`: fastapi uvicorn pandas pydantic anthropic typesafe-sdk langgraph langchain-typesafe mcp pytest httpx.

- [ ] Write `tests/test_model.py::test_plan_id_stable_and_roundtrip` — same assignments in different order → same `id`; `make_wave().to_dict()` has keys `id, airport, window_start, metar, runway_wind, queue, flights, gates, provenance`.
- [ ] Implement; `pip install langgraph langchain-typesafe`; `python -m pytest -q tests/test_model.py` → PASS. Commit.

### Task 2: BTS months → arrivals + wave index (coder)

**Files:** Create `scripts/fetch_bts.py`, `tests/test_waves_index.py`. Output `data/waves/arrivals.csv.gz`, `data/waves/index.csv` (both committed; target < 20 MB total).

**Interfaces:** Consumes `hubs.HUBS, MONTHS`. Produces:
- `arrivals.csv.gz` columns: `date, carrier, flight_no, tail, origin, dest, sched_dep, sched_arr, dep_time, arr_time, arr_delay, weather_delay, nas_delay, late_aircraft_delay, carrier_delay, distance, cancelled, diverted` — rows only where `dest in HUBS`.
- `index.csv` columns: `wave_id, airport, date, hour, n_arrivals, n_disrupted, disrupted_flights` where wave_id = `f"{airport}-{date}-{hour:02d}"`, a wave = 60-min window by `sched_arr` hour with ≥ 1 arrival having `weather_delay > 15 or late_aircraft_delay > 20` and ≥ 3 arrivals total.

- [ ] Source: transtats PREZIP `https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{Y}_{M}.zip` via curl (python SSL fails on this machine). Download to scratch, extract with `-I`-safe handling, never execute archive content.
- [ ] Test `test_index_definition`: every index row has `n_disrupted ≥ 1`, `n_arrivals ≥ 3`, airport in HUBS; ≥ 500 waves total.
- [ ] Run script, run test → PASS. Report row counts and file sizes.

### Task 3: Reference data (coder)

**Files:** Create `scripts/fetch_ref.py`, `tests/test_ref.py`. Outputs in `data/ref/`.

**Produces:**
- `runways.csv`: `airport(IATA), runway(end ident), heading_true, length_ft, le_lat, le_lon, he_lat, he_lon, surface` from OurAirports `runways.csv` + `airports.csv` (one row per runway END; derive heading from ident×10 when missing). HUBS + all alternates in `data/challenge2/airports.csv`.
- `metar.csv`: `station, airport, valid(UTC ISO), local_hour_key("YYYY-MM-DD-HH" local), drct, sknt, gust, vsby, skyc_ceiling_ft, wxcodes, raw` from IEM ASOS `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py` for HUBS, 2024-06-01..2024-08-31, `report_type=3` (routine). Local hour via airport tz (`tz` column of OurAirports? use `zoneinfo` + a dict in the script for the 30 hubs).
- `aircraft.csv`: `tail(N-number with N), type_icao, mfr_model, wake(SUPER|HEAVY|B757|LARGE|SMALL), gate_size(narrow|wide)` from FAA Releasable Aircraft DB (`https://registry.faa.gov/database/ReleasableAircraft.zip`, MASTER + ACFTREF), restricted to tails seen in `arrivals.csv.gz` if present, else all `N` tails of airline types. Wake/gate from a model→type table in the script (A31x/A32x/B73x/E17x/E19x/CRJ = LARGE narrow; B75x = B757 narrow; A33x/A35x/B76x/B77x/B78x = HEAVY wide; A388 = SUPER wide).
- `gates.csv`: `airport, gate_ref, wide(bool)` from Overpass `aeroway=gate` for the top 10 HUBS only (OSM is cut #3; generic fallback in Task 7).

- [ ] Test `test_ref_coverage`: every HUB has ≥ 2 runway ends; METAR rows for ≥ 28 hubs; `aircraft.csv` has ≥ 3000 tails and only allowed wake values.
- [ ] Run, test → PASS. Commit outputs (< 15 MB).

### Task 4: Wind module (coder-simple)

**Files:** Create `atm/wave/wind.py`, `tests/test_wind.py`.

**Produces:**
- `components(wind_dir: float|None, wind_kt: float, gust_kt: float|None, heading_true: float) -> tuple[headwind, crosswind, tailwind]` — gust used for crosswind; `wind_dir None` (VRB/missing) → crosswind = max(wind, gust), headwind 0, tailwind = max(wind,gust) if > 3 else 0.
- `TYPE_XWIND_KT: dict[str,int]` (A320 family 38, B737 33, B757 40, B767 40, B777 38, B787 40, A330 40, A350 40, E175 30, CRJ9 27, default None) with `TYPE_XWIND_SOURCE = "manufacturer demonstrated crosswind (FCOM/AFM, approx.)"`.
- `limits(surface: "dry"|"wet", aircraft_type: str|None) -> tuple[limit_xw, limit_tw, source:str]`.
- `surface_from_metar(wxcodes: list[str]) -> "dry"|"wet"` (any of RA, DZ, SN, TS, GR, PL, FZ, SH → wet).
- `runway_wind(metar, runways, aircraft_type=None) -> list[RunwayWind]`.

- [ ] Tests: `test_crosswind_90deg` (wind 270/20, hdg 360 → xw 20); `test_tailwind` (wind 090/10, hdg 270 → tw 10); `test_faa_wet_limits` (`limits("wet",None)==(15,0,...)`, source contains "8400.9"); `test_type_stricter` (monkeypatch `TYPE_XWIND_KT["TEST"]=12` → `limits("dry","TEST")[0]==12` and source names the type; `limits("dry","A320")[0]==20`); `test_vrb_worst_case` (Review Focus 2).
- [ ] Implement, PASS.

### Task 5: Decision layer with fallback chain (coder → architect review)

**Files:** Create `atm/decide.py`, `tests/test_decide.py`.

**Produces:**
- `DecisionResult` dataclass: `value` (choice id | p_yes float | score int), `probabilities: dict|None`, `confidence: float|None`, `model: str`, `provider: "openrouter"|"typesafe"|"formula"`, `latency_ms: int`, `fallback_from: list[str]`, `errors: list[str]`.
- `class Decider(chain: list[str], timeout_s=2.0)`; chain entries like `"openrouter:liquid/d1-ultra"`, `"typesafe:jev"`, terminal `"formula"` always appended.
  - `choice(state: dict, instructions: str, options: dict[str,str], formula: Callable[[], str]) -> DecisionResult`
  - `noul(state, instructions, formula: Callable[[], float]) -> DecisionResult`
  - `noul_batch(state, items: dict[str,str], formula: Callable[[str], float]) -> dict[str, DecisionResult]` (one request)
  - `score(state, instructions, levels: list[str], formula: Callable[[], int]) -> DecisionResult`
- Transport: `typesafe_sdk.TypeSafeClient(api_key=..., base_url="https://openrouter.ai/api", model=id)` for openrouter; `TypeSafeClient()` for typesafe. Try `langchain_typesafe.TypeSafeClassifier` first only if it supports per-call model + base_url; otherwise SDK directly (record which in a module docstring line). Note: Score answers may be 0-indexed — normalise to 0-based int.
- Module-level breaker per provider entry: 3 consecutive failures → skip 60 s. `DEFAULT_CHAIN` from env `DECISION_CHAIN` (comma list) else `["openrouter:liquid/d1-ultra","openrouter:baseten/elephant-1","typesafe:jev"]`.
- `Decider.on_fallback: Callable[[from_model, to_model, error], None] | None` hook (graph turns it into `MODEL_FALLBACK` messages).

- [ ] Tests (no network; monkeypatch `_call`): `test_falls_through_to_formula_when_all_fail` (Review Focus 4), `test_invalid_choice_is_failure` (Review Focus 5), `test_breaker_opens_after_3`, `test_fallback_hook_called`. One live smoke test marked `@pytest.mark.live` (skipped unless `LIVE=1`).
- [ ] PASS; `LIVE=1 python -m pytest -q -m live tests/test_decide.py` → PASS.

### Task 6: Deploy pipeline (coder)

**Files:** Create `Dockerfile`, `.dockerignore`, `.github/workflows/deploy-space.yml`, `scripts/space_readme_header.md`; modify `atm/server.py` only to mount `frontend/dist` at `/` when it exists (else keep `web/`), behind existing routes.

- Dockerfile: stage `node:20-slim` builds `frontend/` if `frontend/package.json` exists (`npm ci && npm run build`), else creates empty dist; stage `python:3.12-slim` installs `requirements.txt`, copies `atm/ data/ eval/ web/` + dist, `CMD uvicorn atm.server:app --host 0.0.0.0 --port 7860`.
- Workflow: on push to main, `git push --force https://james77mill:$HF_TOKEN@huggingface.co/spaces/james77mill/disruption-negotiator HEAD:main` with Space README header (`sdk: docker`, `app_port: 7860`) prepended in the pushed tree. Large CSVs excluded per `.gitignore`.
- Space creation, Space secrets and `gh secret set HF_TOKEN` are done by the controller **after asking the user**.
- [ ] Verify: `python -c "import atm.server"` OK; workflow YAML parses (`python -c "import yaml,sys;yaml.safe_load(open(...))"`). No local docker; first real build is on HF.

### Task 7: Wave builder (coder)

**Files:** Create `atm/wave/build.py`, `tests/test_build.py`.

**Interfaces:** Consumes Task 1 dataclasses, Task 2/3 files, Task 4 `runway_wind`, `surface_from_metar`. Produces:
- `list_waves(airport: str|None=None, q: str|None=None, limit=50) -> list[dict]` (contract `WaveSummary`).
- `random_wave(airport=None, seed=None) -> dict`.
- `build_wave(wave_id: str) -> Wave` — negotiated flights = disrupted flights + nearest neighbours by `sched_arr` up to 5 (min 3); rest of the hour = `queue` with FCFS runway/slot; runways from `runways.csv` (closed if out of wind limits for a LARGE type, with `closed_reason`); METAR nearest observation ≤ 60 min before window start; aircraft type/wake/gate from `aircraft.csv` (Review Focus 1: unknown → LARGE, narrow, provenance generic); gates from `gates.csv` else generic 10 (2 wide) + `RS`; simulated layers (gate occupancy, fuel extra_min 14-45, crew shift, connections) seeded by `sha256(wave_id+callsign)`; `airborne = sched_dep + 30 min < window_start`.
- Every flight/wave carries `provenance` entries per contract.

- [ ] Tests: `test_build_known_wave` (first index row builds, 3-5 flights, metar present, ≥ 2 runways); `test_unknown_tail_generic` (Review Focus 1); `test_deterministic` (two builds equal).

### Task 8: Rules, moves, utility (architect)

**Files:** Create `atm/wave/rules.py`, `atm/wave/moves.py`, `atm/wave/utility.py`, `tests/test_rules.py`, `tests/test_moves.py`.

**Interfaces:** Consumes Task 1, Task 4. Produces:
- `utility.metrics(wave, a: Assignment) -> FlightMetrics`; `utility.evaluate(wave, plan) -> PlanEvaluation dict` (contract shape: `feasible, checks, utilities, totals`); `utility.agent_utility(wave, plan, agent_id) -> float in [0,1]` (ATC: holding/runway load/taxi; PILOT:<cs>: own fuel/delay/connections; GROUND: overtime/turnaround/reassignment/bussing — weights adapted from v1 `atm/plans.py:utility`).
- `rules.check(wave, plan) -> list[RuleCheck]` covering all 9 RuleIds; between-flight: wake separation on same runway (time-based: HEAVY→LARGE 3 min, SUPER→any 4, else 2, × weather factor), gate overlap incl. queue occupancy; ATC-WIND-LIMITS uses `wind.limits(surface, aircraft_type)`; `rules.feasible(wave, plan) -> bool`.
- `moves.fcfs_plan(wave) -> JointPlan`; `moves.legal_moves(wave, plan, agent_id) -> list[Move]` (authority per spec §6; ≤ 12 options, most useful first); `moves.apply(wave, plan, move) -> JointPlan` (re-slots to earliest legal slot after order/runway changes).

- [ ] Tests on `make_wave()`: `test_wake_violation_detected`, `test_gate_overlap_detected`, `test_wind_rejects_wet_15kt` (METAR wet, xw 18 → fail, evidence has metar raw + limit 15), `test_pilot_cannot_move_other_flight`, `test_ground_delay_only_if_not_airborne`, `test_fcfs_plan_shape`, `test_all_runways_out_of_limits` (Review Focus 3: no land assignment feasible).

### Task 9: Baseline + fallback search (coder)

**Files:** Create `atm/wave/baseline.py`, `atm/wave/fallback.py`, `tests/test_baseline_fallback.py`.

**Interfaces:** Consumes Task 8 (code against its signatures; merge after 8). Produces `baseline.status_quo(wave) -> JointPlan` (FCFS first open runway, scheduled gate with apron wait, divert if hold > extra fuel; may violate GND-CREW-DUTY, reported not hidden); `fallback.solve(wave, budget=400) -> tuple[JointPlan, str]` — beam/greedy search over `legal_moves` from `fcfs_plan`, returns max-min agent utility among feasible plans; if none feasible returns all-divert-or-ground-delay plan; method string e.g. `"beam(width=20) max-min utility"`.
- [ ] Tests: `test_fallback_always_feasible` (incl. Review Focus 3 wave), `test_baseline_matches_fcfs_order`.

### Task 10: LangGraph negotiation + narration (architect)

**Files:** Create `atm/wave/graph.py`, `atm/wave/narrate.py`, `tests/test_graph.py`.

**Interfaces:** Consumes Tasks 5, 8, 9. Produces:
- `negotiate_wave(wave: Wave, mode: "formulas"|"decision"|"decision+claude", chain: list[str]|None=None, on_message: Callable[[dict], None]|None=None) -> dict` (contract `NegotiationResult`, `log` included). Messages are contract `NegotiationMessage` dicts in contract order (§ "Message order"), emitted via `on_message` as produced.
- LangGraph `StateGraph` nodes: `triage, open, moves, counter, check, vote, decide_round, concede, fallback, finalize`; conditional edge `decide_round → finalize | concede | fallback`. `graph.get_graph().draw_mermaid()` saved to `docs/graph.mmd` by a test.
- Each agent's decisions use its own `Decider` call with only its private view as state (Pilot: own flight; ATC: queue + runways + wind; Ground: gates + crew). Reservation per round: `best_own * (0.9 - 0.12*(round-1))`. Triage severity: 0-based score, bonus +2 move options if score ≥ 3.
- `narrate.brief(result) -> dict` (`source, model, brief, lines`) via `anthropic` `claude-opus-5-5`, `messages.parse` with a pydantic model, `output_config={"effort":"low"}`; template on any error.

- [ ] Tests (formulas mode, no network): `test_formulas_run_terminates_valid` (≤ 3 rounds, final plan feasible, all message types legal), `test_providers_down_still_resolves` (chain of failing fake providers → `MODEL_FALLBACK` present, outcome valid), `test_message_order`.
- [ ] Live check: one real wave in `decision` mode completes; report latency and model calls.

### Task 11: API, SSE, replay, contract artefacts (coder)

**Files:** Modify `atm/server.py` (add routes only); create `atm/wave/replay.py`, `frontend/src/api/types.ts`, `frontend/src/api/samples/{config,waves,wave,negotiate_stream,result,benchmark}.json`, `tests/test_api.py`.

**Interfaces:** Consumes Tasks 7, 10 (start with a stub that replays fixture messages). Produces routes exactly per contract: `GET /api/config, /api/waves, /api/waves/random, /api/wave/{id}, /api/runs/{run_id}, /api/benchmark, /api/eval`; `POST /api/negotiate-wave` (SSE `start|message|result|error` frames; `stream:false` → JSON), `POST /api/decision` (v2 body `{run_id, decision, note}`, appends to `eval/decisions.jsonl`; OVERRIDE without note → 422). Runs kept in-memory + `eval/runs/{run_id}.json`. Per-IP limit 10 live runs / 10 min → 429 with `retry_after_s`. `replay.record(wave_ids)` saves runs to `data/replay/`; `replay=true` streams them with 150 ms spacing, no model calls.
- `types.ts` = contract types verbatim; samples = real responses captured from the running server.
- [ ] Tests with `httpx`/`TestClient`: `test_sse_frames_order` (first `start`, last `result`), `test_override_requires_note`, `test_rate_limit_429`, `test_unknown_wave_404`.
- [ ] Commit types/samples with message prefix `contract:` if anything differs from the doc.

### Task 12: Benchmark, outage test, ablation (coder)

**Files:** Create `atm/wave/benchmark.py`, `tests/test_benchmark_small.py`. Outputs `eval/benchmark.json` (contract `BenchmarkReport`), `eval/BENCHMARK.md`.

- CLI: `python -m atm.wave.benchmark --n 50 --arms openrouter:typesafe/jev-1.14,openrouter:liquid/d1-ultra,openrouter:baseten/elephant-1,formula --concurrency 16 --seed 7`. Same 50 waves (seeded sample of index, stratified by airport) for all arms; each arm's chain = [arm, "formula"].
- Metrics per contract `summary`; Brier vs formula verdict; agreement = same final plan id as formulas arm; cost from OpenRouter usage if returned else tokens × list price.
- Outage: rerun 20 waves with chain `["openrouter:invalid/model", winner, "formula"]` → `outage_test`. Ablation: engine off (skip rules in move/vote) → % agreed plans with violations.
- Selection: winner = best (delay, pax_protected) with error_rate < 2% and p95 < 1000 ms; else best on outcome with the threshold miss stated in `selection.reasons`. Write winner into `eval/benchmark.json`; `Config.default_model` reads it.
- [ ] `test_benchmark_small`: `--n 2 --arms formula` produces a valid report. Then the full run (background), report numbers honestly.

### Task 13: MCP endpoint (coder-simple; cut #6)

**Files:** Create `atm/mcp_server.py`; modify `atm/server.py` to mount at `/mcp`.
- FastMCP tools wrapping `build.list_waves`, `build.build_wave`, `moves.legal_moves`, `rules.check`, `utility.evaluate` (read-only), plus `negotiate_wave(formulas)`. Verify with `mcp` client listing tools.

### Final (controller)
- Record 5 replay waves (Task 11 `replay.record`), push, create Space after user OK, set secrets, verify public URL, update README (architecture diagram, benchmark table, limits, failure case), whole-branch review agent (architect), submit before 16:00.
