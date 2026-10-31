# API contract: Disruption Negotiator v2 (wave mode)

Status: **v2.0, 31 Oct 2026 13:40** (challenge rules_of_play: exactly 3 agents ATC / PILOT:<focus> / GROUND,
one `AGENT_TURN` message per agent per turn, max 3 turns; `MOVE`/`MOVE_REJECTED`/`COUNTER`/`VOTE` superseded;
result gains `revision`, `limits`, `turns`, `agents`, `focus`; `Wave.focus`; `GET /api/flights`; new rules
`ATC-RWY-CAPACITY`, `GND-GATE-NOTICE`; workbook limits). v1.2 (12:55): added `Triage`; `/api/eval` marked v1;
benchmark brier/latency/ablation nullable. Frozen at 12:30 for the frontend; any later change is
announced in the commit message with the prefix `contract:`. Design: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md`.

The frontend (Vite + React + TS, `frontend/`) talks only to these endpoints. In dev, Vite proxies
`/api` to `http://localhost:8000`. In production the same FastAPI service serves `frontend/dist` at `/`.
Once the backend lands, the exact types live in `frontend/src/api/types.ts` and one real sample
response per endpoint in `frontend/src/api/samples/*.json`. Until then, build against this doc.

## Conventions

- JSON, `snake_case` keys. Times on the wave timeline are **minutes relative to the wave window
  start `T0`** (`*_min`, may be negative); every entity also carries ISO strings for display.
- `AgentId`: `"ATC" | "GROUND" | "PILOT:<callsign>"`. Since v2.0 exactly **three** agents negotiate:
  `ATC`, `PILOT:<WaveScenario.focus>` (the pilot of the disruption flight only) and `GROUND`
  (`NegotiationResult.agents`). Other wave flights have no pilot agent: ATC sequences them (runway,
  slot/holding, speed control or ground delay as ATC instructions), GROUND assigns their gates, and the
  engine still checks all their rules. Non-agent senders: `"ENGINE"` (safety rules / protocol) and
  `"DECIDER"` (wave triage).
- Errors: non-2xx status with `{ "error": { "code": string, "message": string } }`.
  `429` adds `retry_after_s`. Rate limit: 10 live negotiations per 10 min per IP.
- Every real-world fact carries a `Provenance` so the UI can badge real vs simulated.

```ts
type AgentId = "ATC" | "GROUND" | `PILOT:${string}`;
type Sender = AgentId | "ENGINE" | "DECIDER";

interface Provenance {
  layer: "flights" | "queue" | "runways" | "weather" | "aircraft" | "gates" | "fuel" | "crew" | "connections" | "gate_occupancy";
  status: "real" | "simulated" | "generic";   // generic = template used because real data missing
  source: string;                            // e.g. "BTS On-Time 2024-07", "METAR KATL 151651Z", "FAA Registry"
  detail?: string | null;
}
```

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/config` | `Config` |
| GET | `/api/waves?airport=ATL&q=DL123&limit=50` | `WaveSummary[]` |
| GET | `/api/waves/random?airport=ATL` | `WaveSummary` |
| GET | `/api/flights?airport=ATL&q=DL123&limit=50&offset=0` | `{ flights: FlightRow[]; total: number }` (rows of the 2015 challenge flights.csv) |
| GET | `/api/wave/{wave_id}` | `WaveScenario` (`wave_id` may be a BTS wave id or `"F2015-<row_id>"`) |
| POST | `/api/negotiate-wave` | SSE stream of `NegotiationEvent` (or `NegotiationResult` with `stream:false`) |
| GET | `/api/runs/{run_id}` | `NegotiationResult` (with full `log`) |
| POST | `/api/decision` | `{ ok: true, recorded_at: string }` |
| GET | `/api/benchmark` | `BenchmarkReport` (404 until generated) |
| GET | `/api/eval` | v1 single-flight eval (legacy shape; not used by the React app) |
| GET | `/mcp` | MCP server (not used by the frontend) |

v1 single-flight endpoints (`/api/negotiate`, `/api/random`, …) stay as they are for `web/index.html`; the React app does not use them.

### GET /api/config

```ts
interface Config {
  version: string;
  jev_available?: boolean;            // legacy v1 flag, still sent
  model?: string;                     // narration model id (legacy v1 field)
  policies?: string[]; n_disruptions?: number;   // legacy v1 fields
  modes: Mode[];                       // available modes
  default_mode: Mode;
  models: ModelInfo[];                 // decision models, benchmark winner first
  default_model: string;
  llm_available: boolean;              // Claude narration available
  tracing: boolean;                    // LangSmith on
  replay_available: boolean;           // pre-recorded waves exist
}
type Mode = "formulas" | "decision" | "decision+claude";
interface ModelInfo {
  id: string;                          // e.g. "liquid/d1-ultra", "typesafe/jev-1.14"
  provider: "openrouter" | "typesafe" | "formula";
  label: string;                       // "LiquidAI d1-ultra"
  available: boolean;
  benchmark_rank?: number;             // 1 = winner
}
```

### GET /api/waves, /api/waves/random

```ts
interface WaveSummary {
  id: string;                          // "ATL-2024-07-15-16"
  airport: { iata: string; icao: string; name: string };
  window_start: string;                // ISO local time
  window_end: string;
  n_flights: number;                   // negotiated flights (3-5)
  n_queue: number;                     // other real arrivals in the window
  callsigns: string[];
  disrupted_callsigns: string[];
  weather_summary: string;             // "21024G33KT 3SM TSRA, wet"
  replay_available: boolean;
}
```

### GET /api/flights

Rows of the challenge `flights.csv` (2015, cancelled rows skipped), filtered by destination airport
and free text (callsign, tail, origin, dest, wave id). Picking a row = the challenge input: its
`wave_id` `"F2015-<row_id>"` works everywhere a wave id is accepted (`GET /api/wave/{id}`,
`POST /api/negotiate-wave`); that row's flight becomes `WaveScenario.focus`.

```ts
interface FlightRow {
  row_id: number;
  wave_id: string;                     // "F2015-<row_id>"
  date: string;                        // "2015-01-01"
  carrier: string; flight: string;     // "AA", "AA1234"
  origin: string; dest: string;
  sched_arr: string;                   // "HH:MM" local
  arr_delay: number; weather_delay: number; late_aircraft_delay: number;   // minutes
}
```

### GET /api/wave/{wave_id}

```ts
interface WaveScenario {
  id: string;
  airport: Airport;
  window_start: string;                // ISO; T0 = this instant
  metar: Metar | null;
  runway_wind: RunwayWind[];           // one per runway end
  queue: QueueArrival[];               // fixed real traffic in the window (not negotiated)
  flights: WaveFlight[];               // negotiated flights
  gates: Gate[];
  severity?: Triage | null;                 // filled once a negotiation has triaged it
  focus: string | null;                // v2.0: callsign of the disruption flight (its pilot is the PILOT agent);
                                       // null => the engine uses the most-delayed disrupted flight
  provenance: Provenance[];            // per layer for the whole wave
}

interface Triage { score: number; levels: string[]; probabilities: number[]; confidence?: number }  // 0-based score

interface Airport {
  iata: string; icao: string; name: string; lat: number; lon: number;
  runways: Runway[];
}
interface Runway {
  id: string;                          // runway end, e.g. "08L"
  heading_true: number;                // degrees
  length_ft: number;
  open: boolean;
  closed_reason?: string | null;            // "crosswind 24 kt exceeds FAA 8400.9 §6d (wet, 15 kt)"
  // for the stretch map:
  le_lat?: number; le_lon?: number; he_lat?: number; he_lon?: number;
}
interface Metar {
  raw: string;                         // full METAR text
  station: string; observed_at: string;
  wind_dir_deg: number | null; wind_kt: number; gust_kt: number | null;
  visibility_sm: number | null; ceiling_ft: number | null;
  wx_codes: string[];                  // ["TSRA"]
  surface: "dry" | "wet";
}
interface RunwayWind {
  runway: string;
  headwind_kt: number; crosswind_kt: number; tailwind_kt: number;  // gust-inclusive crosswind
  limit_crosswind_kt: number; limit_tailwind_kt: number;
  limit_source: string;                // "FAA Order 8400.9 §6d (wet)" or "A320 demonstrated 38 kt"
  within_limits: boolean;
}
interface QueueArrival {
  callsign: string; aircraft_type: string | null; wake: Wake;
  runway: string; slot_min: number;
}
type Wake = "SUPER" | "HEAVY" | "B757" | "LARGE" | "SMALL";

interface WaveFlight {
  callsign: string;                    // "DL1234"
  carrier: string; airline: string; flight_no: number; tail: string | null;
  aircraft_type: string | null;        // "A321", null if unknown
  wake: Wake; gate_size: "narrow" | "wide";
  origin: string;
  sched_arr: string;                   // ISO local
  sched_min: number;                   // scheduled arrival vs T0
  eta_earliest_min: number;
  airborne: boolean;                   // false => ground delay at origin possible
  disrupted: boolean;
  delays: { weather: number; late_aircraft: number; nas: number; carrier: number; arr_delay: number }; // real BTS minutes
  fuel: { extra_min: number; alternate: string; alternate_min: number; final_reserve_min: number };
  connections: { flight: string; pax: number; departs_min: number }[];
  scheduled_gate: string;
  crew_shift_end_min: number;
  next_departure_min: number;
  provenance: Provenance[];
}
interface Gate {
  id: string; size: "narrow" | "wide"; pier: string; remote: boolean;
  busy: { from_min: number; to_min: number; label: string }[];   // occupancy by non-wave aircraft
}
```

### Plans, moves, rules (shared by stream and results)

```ts
interface JointPlan {
  id: string;                          // stable hash of assignments
  label: string;                       // "DL1234 R08L T+6 A12 · AA88 R09R T+9 RS · …"
  assignments: Assignment[];           // one per WaveFlight
}
interface Assignment {
  callsign: string;
  action: "land" | "divert";
  runway?: string; slot_min?: number;  // landing slot vs T0
  gate?: string;                       // "RS" = remote stand
  speed_reduction_min: number;         // 0-8, absorbed en route
  ground_delay_min: number;            // >0 only if not airborne
  metrics: FlightMetrics;
}
interface FlightMetrics {
  hold_min: number; fuel_kg: number; delay_min: number; in_block_min: number | null;
  pax_protected: number; pax_total: number; overtime_min: number; next_dep_delay_min: number;
}
interface RuleCheck {
  rule: RuleId;
  owner: "ATC" | "PILOT" | "GROUND";
  callsign?: string;                   // flight the check is about
  passed: boolean;
  detail: string;                      // human-readable, cites values
  source: string;                      // "FAA Order 8400.9 §6d", "challenge2_operational_constraints.xlsx wake_separation ...", "ops rule"
  evidence: Record<string, string | number>;   // e.g. { metar: "...", crosswind_kt: 24, limit_kt: 15 }
}
type RuleId = "ATC-RWY-OPEN" | "ATC-WAKE-SEP" | "ATC-TAXI-ROUTE" | "ATC-WIND-LIMITS"
  | "ATC-RWY-CAPACITY"                 // v2.0: landings per physical runway in the rolling 60 min <= workbook cap
  | "PLT-EARLIEST-ETA" | "PLT-FUEL-RESERVE" | "GND-GATE-SIZE" | "GND-GATE-FREE" | "GND-CREW-DUTY"
  | "GND-GATE-NOTICE";                 // v2.0: gate change only if arrival >= T+20 (remote stand exempt)
// v2.0: GND-CREW-DUTY never fails any more (workbook: overtime after the 8 h shift is allowed at 1.5x cost);
// it reports overtime. PLT-CREW-DUTY (13 h) is not enforced: no crew report time in the data (see `limits`).

interface PlanEvaluation {
  plan_id: string;
  feasible: boolean;
  checks: RuleCheck[];                 // all rules, all flights
  utilities: Record<AgentId, number>;  // 0..1 for the 3 negotiating agents (formula decision model)
  totals: { delay_min: number; fuel_kg: number; hold_min: number; pax_protected_pct: number;
            overtime_min: number; diversions: number };
}
interface Move {
  id: string;
  agent: AgentId;
  kind: "swap_order" | "change_runway" | "shift_slot"                         // ATC
      | "speed_reduction" | "ground_delay" | "request_divert" | "accept"      // PILOT (own flight);
                                                                             // v2.0: ATC may also issue speed_reduction /
                                                                             // ground_delay for non-focus flights
      | "reassign_gate" | "remote_stand" | "swap_gates";                     // GROUND
  callsigns: string[];
  params: Record<string, string | number>;   // { runway: "09R" } | { minutes: 6 } | { gate: "B4" }
  label: string;                       // "Swap landing order DL1234 ↔ AA88"
}
```

### POST /api/negotiate-wave

Request:

```ts
interface NegotiateRequest {
  wave_id: string;
  mode: Mode;
  model?: string;                      // primary decision model; default = Config.default_model
  replay?: boolean;                    // true = stream a pre-recorded run, no model calls; waves without a recording run live
  stream?: boolean;                    // default true
}
```

Response (`stream` true): `Content-Type: text/event-stream`. It's a POST, so use `fetch` with a
`ReadableStream` reader (or `@microsoft/fetch-event-source`), not `EventSource`. Each SSE frame:

```
event: <start|message|result|error>
data: <JSON>
```

```ts
type NegotiationEvent =
  | { event: "start";   data: { run_id: string; wave_id: string; mode: Mode; model_chain: string[]; started_at: string } }
  | { event: "message"; data: NegotiationMessage }
  | { event: "result";  data: NegotiationResult }
  | { event: "error";   data: { code: string; message: string } };

interface NegotiationMessage {
  seq: number;                         // 0,1,2,… in order
  round: number;                       // the turn: 0 = triage/opening, 1..3
  ts: string;
  sender: Sender;
  type: MessageType;
  body: MessageBody;                   // discriminated by `type`, see below
  rationale: string;                   // one line, shown under the message
  decision?: DecisionMeta;             // present when a model/formula made a decision
}
type MessageType = "TRIAGE" | "OPEN" | "AGENT_TURN" | "CHECK" | "CONSENSUS" | "NO_CONSENSUS"
  | "FALLBACK" | "MODEL_FALLBACK"
  | "MOVE" | "MOVE_REJECTED" | "COUNTER" | "VOTE";   // superseded in v2.0 (never emitted), kept for old runs

interface DecisionMeta {
  kind: "choice" | "noul" | "score" | "formula";
  model: string;                       // model that actually answered
  provider: "openrouter" | "typesafe" | "formula";
  latency_ms: number;
  probabilities?: Record<string, number>;   // choice/score distribution
  confidence?: number;
  fallback_from?: string[];            // models that failed before this one answered
}

type MessageBody =
  | { type: "TRIAGE"; score: number; levels: string[]; probabilities: number[]; confidence?: number }
  | { type: "OPEN"; plan: JointPlan; evaluation: PlanEvaluation;          // ENGINE: FCFS opening plan (incumbent of turn 1)
      agents: AgentId[]; focus: string }
  | AgentTurnBody                                                          // v2.0: the ONE message per agent per turn
  | { type: "CHECK"; plan_id: string; passed: boolean; violations: RuleCheck[]; proposed_by?: AgentId }
  | { type: "CONSENSUS"; plan_id: string; accepted_by: AgentId[]; turn: number }
  | { type: "NO_CONSENSUS"; valid_plan_ids: string[]; next_reservation: Record<AgentId, number>;
      turn: number; reason: string }
  | { type: "FALLBACK"; plan_id: string; method: string; reason: string }  // reason: why no consensus (who rejected what)
  | { type: "MODEL_FALLBACK"; agent: AgentId; from_model: string; to_model: string; error: string }
  // superseded in v2.0, never emitted by new runs:
  | { type: "MOVE"; move: Move; plan: JointPlan; options_count: number }
  | { type: "MOVE_REJECTED"; move: Move; violations: RuleCheck[] }
  | { type: "COUNTER"; plan: JointPlan; evaluation: PlanEvaluation }
  | { type: "VOTE"; votes: LegacyVote[] };

// v2.0: one structured message per agent per turn (sender = agent, round = turn)
interface AgentTurnBody {
  type: "AGENT_TURN";
  turn: number;                        // 1..3
  agent: AgentId;
  role: "ATC" | "PILOT" | "GROUND";
  moves: AppliedMove[];                // moves applied this turn, in order (0-3), each engine-checked
  rejected: RejectedMove[];            // moves the engine vetoed this turn (agent re-picked)
  proposal: JointPlan;                 // the agent's plan for the table after its moves (metrics filled)
  proposal_utility: number;            // the agent's own utility for its proposal
  votes: Vote[];                       // on every CHECK-passed plan on the table when it speaks
  rationale: string;
  decision?: DecisionMeta;             // the vote decision (moves carry their own)
}
interface AppliedMove { move: Move; utility_before: number; utility_after: number; decision: DecisionMeta }
interface RejectedMove { move: Move; violations: RuleCheck[]; reason: string; decision: DecisionMeta }
interface Vote {
  plan_id: string;
  vote: "ACCEPT" | "REJECT";
  p_accept?: number;                   // absent in formulas mode
  utility: number; reservation: number;
  reason: string;
}
interface LegacyVote { plan_id: string; decision: "ACCEPT" | "REJECT"; p_accept?: number;
                       utility: number; reservation: number; reason: string }

interface NegotiationResult {
  run_id: string; wave_id: string; mode: Mode; model: string;
  outcome: "consensus" | "fallback";
  rounds: number;                      // = turns (kept for v1.x clients)
  turns: number;                       // v2.0: 1..3
  agents: AgentId[];                   // v2.0: ["ATC", "PILOT:<focus>", "GROUND"]
  focus: string;                       // v2.0: callsign of the disruption flight
  revision: Revision;                  // v2.0: the challenge output for the focus flight
  limits: Limit[];                     // v2.0: every limit used, kept or changed, with its source
  final_plan: JointPlan;
  evaluation: PlanEvaluation;          // all checks pass by construction
  baseline: { plan: JointPlan; evaluation: PlanEvaluation };  // status quo FCFS
  narration: { source: "claude" | "template"; model?: string; brief: string; lines: string[] } | null;
  model_errors: string[];
  stats: { model_calls: number; fallbacks: number; elapsed_ms: number };
  log?: NegotiationMessage[];          // included when stream=false and in GET /api/runs/{id}
}

interface Revision {
  callsign: string;
  action: "land" | "hold" | "ground_delay" | "divert";   // hold = airborne holding > 0
  revised_arrival: string | null;      // "HH:MM" local (window_start + slot); null when diverting
  revised_arrival_min: number | null;  // landing slot vs T0
  revised_arrival_t?: string;          // "T+12"
  runway: string | null; gate: string | null;
  scheduled_arrival: string;           // "HH:MM" local
  scheduled_gate: string;
  speed_reduction_min?: number; ground_delay_min?: number; hold_min?: number;
  alternate?: string | null;           // divert only
  delay_vs_schedule_min: number;       // landing slot - scheduled arrival (divert: estimated delay)
  summary: string;                     // one line for the UI
}
interface Limit {
  limit: string;                       // "wake separation (same runway)"
  value: string;                       // "Heavy->any 120 s, ..."
  source: "workbook" | "real data" | "assumption";
  note: string;                        // what we changed / why
}
```

Message order (v2.0). Round 0: `TRIAGE`, `OPEN`. Each turn 1..3:
`AGENT_TURN(ATC)` → `CHECK` (if its proposal is new) → `AGENT_TURN(PILOT:<focus>)` → `CHECK` →
`AGENT_TURN(GROUND)` → `CHECK` → `CONSENSUS` or `NO_CONSENSUS`. Exactly one `AGENT_TURN` per agent
per turn. `FALLBACK` only after `NO_CONSENSUS` of turn 3. `MODEL_FALLBACK` can appear anywhere.

Protocol rules: in turn t ATC starts from the incumbent plan, PILOT from ATC's proposal, GROUND from
PILOT's. Each agent votes on every CHECK-passed plan on the table when it speaks (incumbent, proposals
earlier in this turn, its own proposal). Consensus = one CHECK-passed plan ACCEPTed by all 3 agents in
the same turn. Otherwise the best CHECK-passed plan of the turn becomes the next incumbent; after turn
3 the deterministic non-AI solver decides and `FALLBACK.reason` says why. Limits come from the
challenge workbook (`data/challenge2/challenge2_operational_constraints.xlsx`); every limit kept,
changed or added is listed in `NegotiationResult.limits`.

### POST /api/decision

```ts
interface DecisionRequest { run_id: string; decision: "APPROVE" | "OVERRIDE"; note?: string } // note required for OVERRIDE
```

### GET /api/benchmark

```ts
interface BenchmarkReport {
  generated_at: string; n_waves: number; seed: number;
  n_common?: number; wave_ids?: string[]; source?: string;   // waves every arm finished; sampled ids; bts | flights2015
  arms: {
    model: string; provider: string; label: string;
    summary: {
      consensus_rate_pct: number; mean_rounds: number; fallback_rate_pct: number;
      rule_violations_in_agreed_plans: number;
      delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number;
      agreement_with_formula_pct: number; brier: number | null;
      latency_p50_ms: number | null; latency_p95_ms: number | null; error_rate_pct: number; cost_usd_per_wave: number;
    };
    delay_min_vs_feasible_status_quo?: number;   // arm mean delay on waves where status quo is legal
    n_ok?: number; failed_ids?: string[]; p95_all_ms?: number | null; model_decisions?: number;
  }[];
  baseline: { delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number; diversions: number;
    n_feasible?: number; infeasible_ids?: string[];   // status quo legal on n_feasible waves
    feasible_only?: { delay_min: number; fuel_kg: number; pax_protected_pct: number; overtime_min: number; diversions: number } };
  ablation_no_engine_unsafe_pct: number | null;   // null = not run
  outage_test: { primary_disabled: string; waves: number; resolved: number; fallbacks_used: Record<string, number> };
  selection: { winner: string; rule: string; reasons: string[] };
  failures: { wave_id: string; model: string; reason: string }[];   // clickable: open the wave
}
```

`GET /api/eval` is the frozen v1 single-flight evaluation; the React app uses `/api/benchmark` only.

## Screens the contract is meant to support (from the design)

- **Hero:** runway + gate timeline. Lanes = runways (from `Airport.runways` that are open) and gates
  (`Gate[]`); bars = `queue` (grey, fixed) and wave flights (`Assignment.slot_min`, `in_block_min`);
  animate between consecutive `AGENT_TURN.proposal` plans; flash red on each `AGENT_TURN.rejected[]`
  item with `violations[0].detail`.
- Flight picker (`/api/flights`) and wave picker, mode + model selector, triage card, revision card
  (`revision.summary`), consensus log by turn (one card per `AGENT_TURN`; colour per agent;
  `decision` shows model, probability, latency, and `fallback_from` as a warning), per-flight
  provenance badges, wind table (`runway_wind`), decision panel (final plan per flight, utilities,
  full `checks` with `source` + `evidence`, before/after vs `baseline`, regressions in red), Approve /
  Override, Claude brief, Benchmark tab.
- Stretch: airport map from runway end coordinates + METAR wind arrow.
