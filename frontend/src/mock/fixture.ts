// Offline demo wave (shape = docs/api-contract.md). The flights are modelled on a real BTS-style
// disrupted hour at ATL; gate occupancy, fuel, crew and connections are simulated, as the backend's are.
// Every metric and total is computed by ./engine from the assignments.
import type {
  AgentId, Config, NegotiationMessage, NegotiationResult, WaveScenario, WaveSummary, MessageBody,
  DecisionMeta, Sender, RuleCheck, Move, BenchmarkReport,
} from "../api/types";
import { checksFor, evaluate, plan, type Draft } from "./engine";

const T0 = "2024-07-15T16:00:00-04:00";
const iso = (min: number) => new Date(Date.parse(T0) + min * 60000).toISOString();
const METAR = "KATL 151952Z 25018G26KT 3SM +TSRA BKN012 OVC025 27/22 A2992";

const prov = (layer: any, status: "real" | "simulated" | "generic", source: string, detail?: string) => ({ layer, status, source, detail });

export const wave: WaveScenario = {
  id: "ATL-2024-07-15-16",
  airport: {
    iata: "ATL", icao: "KATL", name: "Hartsfield–Jackson Atlanta International", lat: 33.6367, lon: -84.4281,
    runways: [
      { id: "26R", heading_true: 270, length_ft: 9000, open: true },
      { id: "27L", heading_true: 270, length_ft: 9000, open: true },
      { id: "28", heading_true: 270, length_ft: 9000, open: true },
      { id: "26L", heading_true: 270, length_ft: 10000, open: false, closed_reason: "Runway inspection until T+45 (simulated)" },
    ],
  },
  window_start: T0,
  metar: {
    raw: METAR, station: "KATL", observed_at: "2024-07-15T15:52:00-04:00",
    wind_dir_deg: 250, wind_kt: 18, gust_kt: 26, visibility_sm: 3, ceiling_ft: 1200, wx_codes: ["+TSRA"], surface: "wet",
  },
  runway_wind: ["26R", "27L", "28", "26L"].map((runway) => ({
    runway, headwind_kt: 17, crosswind_kt: 9, tailwind_kt: 0,
    limit_crosswind_kt: 15, limit_tailwind_kt: 0, limit_source: "FAA Order 8400.9 §6d (wet)", within_limits: true,
  })),
  queue: [
    ["DL1909", "A321", "26R", 2], ["UA1832", "A320", "26R", 6], ["DL2201", "B712", "26R", 12], ["F91450", "A20N", "26R", 15],
    ["DL837", "B752", "26R", 23], ["WN1188", "B38M", "26R", 27], ["AA2205", "A321", "26R", 33], ["DL1350", "A321", "26R", 38],
    ["NK212", "A20N", "26R", 44], ["DL2890", "B739", "26R", 51],
    ["DL442", "A321", "27L", 3], ["AA1033", "A319", "27L", 8], ["DL1504", "B712", "27L", 13], ["DL1622", "A320", "27L", 18],
    ["UA2140", "B39M", "27L", 24], ["DL615", "A321", "27L", 31], ["B61215", "A320", "27L", 35], ["DL2077", "B739", "27L", 40],
    ["DL1983", "A321", "27L", 47],
    ["DL1077", "CRJ9", "28", 4], ["AS640", "B739", "28", 10], ["DL2245", "A321", "28", 16], ["DL398", "B763", "28", 21],
    ["AA1918", "A321", "28", 34], ["DL2560", "B712", "28", 38], ["F92210", "A20N", "28", 44], ["DL1201", "A321", "28", 50],
  ].map(([callsign, type, runway, slot]) => ({
    callsign: callsign as string, aircraft_type: type as string, wake: type === "B763" ? "HEAVY" : type === "B752" ? "B757" : "LARGE",
    runway: runway as string, slot_min: slot as number,
  })) as WaveScenario["queue"],
  flights: [
    {
      callsign: "DL1172", carrier: "DL", airline: "Delta Air Lines", flight_no: 1172, tail: "N391DN", aircraft_type: "A321",
      wake: "LARGE", gate_size: "narrow", origin: "MCO", sched_arr: iso(8), sched_min: 8, eta_earliest_min: 19, airborne: true,
      disrupted: true, delays: { weather: 31, late_aircraft: 0, nas: 4, carrier: 0, arr_delay: 35 },
      fuel: { extra_min: 22, alternate: "BHM", alternate_min: 34, final_reserve_min: 30 },
      connections: [{ flight: "DL905 BOS", pax: 9, departs_min: 62 }, { flight: "DL2210 LGA", pax: 14, departs_min: 75 }],
      scheduled_gate: "A12", crew_shift_end_min: 95, next_departure_min: 70,
      provenance: [prov("flights", "real", "BTS On-Time 2024-07"), prov("aircraft", "real", "FAA Registry N391DN"),
        prov("fuel", "simulated", "seeded"), prov("connections", "simulated", "seeded"), prov("crew", "simulated", "seeded")],
    },
    {
      callsign: "DL2384", carrier: "DL", airline: "Delta Air Lines", flight_no: 2384, tail: "N812DN", aircraft_type: "B739",
      wake: "LARGE", gate_size: "narrow", origin: "DFW", sched_arr: iso(12), sched_min: 12, eta_earliest_min: 24, airborne: true,
      disrupted: true, delays: { weather: 0, late_aircraft: 38, nas: 6, carrier: 0, arr_delay: 44 },
      fuel: { extra_min: 18, alternate: "CHA", alternate_min: 28, final_reserve_min: 30 },
      connections: [{ flight: "DL1188 MSY", pax: 6, departs_min: 70 }],
      scheduled_gate: "A14", crew_shift_end_min: 140, next_departure_min: 64,
      provenance: [prov("flights", "real", "BTS On-Time 2024-07"), prov("aircraft", "real", "FAA Registry N812DN"),
        prov("fuel", "simulated", "seeded"), prov("connections", "simulated", "seeded"), prov("crew", "simulated", "seeded")],
    },
    {
      callsign: "DL72", carrier: "DL", airline: "Delta Air Lines", flight_no: 72, tail: "N813NW", aircraft_type: "A333",
      wake: "HEAVY", gate_size: "wide", origin: "AMS", sched_arr: iso(20), sched_min: 20, eta_earliest_min: 27, airborne: true,
      disrupted: true, delays: { weather: 24, late_aircraft: 0, nas: 3, carrier: 0, arr_delay: 27 },
      fuel: { extra_min: 25, alternate: "BHM", alternate_min: 34, final_reserve_min: 30 },
      connections: [{ flight: "DL618 BNA", pax: 9, departs_min: 72 }, { flight: "DL1440 RDU", pax: 22, departs_min: 80 }],
      scheduled_gate: "E5", crew_shift_end_min: 50, next_departure_min: 135,
      provenance: [prov("flights", "real", "BTS On-Time 2024-07"), prov("aircraft", "real", "FAA Registry N813NW"),
        prov("fuel", "simulated", "seeded"), prov("connections", "simulated", "seeded"), prov("crew", "simulated", "seeded")],
    },
    {
      callsign: "DL1611", carrier: "DL", airline: "Delta Air Lines", flight_no: 1611, tail: "N135EV", aircraft_type: "CRJ9",
      wake: "LARGE", gate_size: "narrow", origin: "TYS", sched_arr: iso(30), sched_min: 30, eta_earliest_min: 31, airborne: false,
      disrupted: false, delays: { weather: 0, late_aircraft: 0, nas: 1, carrier: 0, arr_delay: 1 },
      fuel: { extra_min: 30, alternate: "CHA", alternate_min: 24, final_reserve_min: 30 },
      connections: [],
      scheduled_gate: "A16", crew_shift_end_min: 180, next_departure_min: 66,
      provenance: [prov("flights", "real", "BTS On-Time 2024-07"), prov("aircraft", "generic", "type from schedule, tail not in FAA Registry"),
        prov("fuel", "simulated", "seeded"), prov("crew", "simulated", "seeded")],
    },
  ],
  gates: [
    { id: "A12", size: "narrow", pier: "A", remote: false, busy: [{ from_min: 0, to_min: 41, label: "DL1490 late outbound" }] },
    { id: "A14", size: "narrow", pier: "A", remote: false, busy: [{ from_min: 75, to_min: 110, label: "DL2771" }] },
    { id: "A16", size: "narrow", pier: "A", remote: false, busy: [{ from_min: 0, to_min: 36, label: "DL330 turn" }] },
    { id: "E5", size: "wide", pier: "E", remote: false, busy: [{ from_min: 0, to_min: 40, label: "KE35 turn" }] },
    { id: "E7", size: "wide", pier: "E", remote: false, busy: [{ from_min: 0, to_min: 22, label: "DL50 turn" }] },
    { id: "RS", size: "wide", pier: "Remote", remote: true, busy: [] },
  ],
  provenance: [
    prov("flights", "real", "BTS On-Time 2024-07"), prov("queue", "real", "BTS On-Time 2024-07, ATL arrivals 16:00–17:00"),
    prov("runways", "real", "OurAirports runways.csv"), prov("weather", "real", `METAR ${METAR.slice(0, 12)}`),
    prov("gates", "real", "OpenStreetMap aeroway=gate"), prov("gate_occupancy", "simulated", "seeded per wave"),
  ],
};

// ---- plans ----
const d = (callsign: string, runway: string, slot_min: number, gate: string, ground_delay_min = 0): Draft =>
  ({ callsign, action: "land", runway, slot_min, gate, speed_reduction_min: 0, ground_delay_min });

const P0 = plan("p0-fcfs", [d("DL1172", "26R", 19, "A12"), d("DL2384", "27L", 26, "A14"), d("DL72", "26R", 29, "E5"), d("DL1611", "28", 31, "A16")], wave);
const P1a = plan("p1-atc", [d("DL1172", "26R", 19, "A12"), d("DL2384", "27L", 26, "A14"), d("DL72", "28", 27, "E5"), d("DL1611", "28", 31, "A16")], wave);
const P1g = plan("p1-gnd", [d("DL1172", "26R", 19, "RS"), d("DL2384", "27L", 26, "A14"), d("DL72", "28", 27, "E7"), d("DL1611", "28", 31, "A16")], wave);
const P2 = plan("p2-gnd", [d("DL1172", "26R", 19, "A14"), d("DL2384", "27L", 26, "A12"), d("DL72", "28", 27, "E7"), d("DL1611", "28", 31, "A16")], wave);

type U = Record<AgentId, number>;
const u = (atc: number, gnd: number, a: number, b: number, c: number, e: number): U =>
  ({ ATC: atc, GROUND: gnd, "PILOT:DL1172": a, "PILOT:DL2384": b, "PILOT:DL72": c, "PILOT:DL1611": e });
const E0 = evaluate(P0, wave, u(0.58, 0.41, 0.31, 0.72, 0.44, 0.66));
const E1a = evaluate(P1a, wave, u(0.69, 0.47, 0.31, 0.72, 0.58, 0.66));
const E1g = evaluate(P1g, wave, u(0.69, 0.62, 0.44, 0.72, 0.81, 0.66));
const E2 = evaluate(P2, wave, u(0.69, 0.71, 0.84, 0.52, 0.81, 0.66));

// a rejected-move check for one flight under a hypothetical plan
const violation = (drafts: Draft[], rule: string, callsign: string): RuleCheck[] =>
  checksFor(plan("tmp", drafts, wave), wave).filter((c) => c.rule === rule && c.callsign === callsign && !c.passed);

// ---- log ----
const MODEL = "liquid/d1-ultra", JEV = "typesafe/jev-1.14";
let seq = 0;
const log: NegotiationMessage[] = [];
const msg = (round: number, sender: Sender, body: MessageBody, rationale: string, decision?: DecisionMeta) =>
  log.push({ seq: seq++, round, ts: new Date(Date.parse("2024-07-15T15:58:00-04:00") + seq * 900).toISOString(), sender, type: body.type, body, rationale, decision });
const choice = (latency_ms: number, probabilities?: Record<string, number>, extra: Partial<DecisionMeta> = {}): DecisionMeta =>
  ({ kind: "choice", model: MODEL, provider: "openrouter", latency_ms, probabilities, ...extra });
const mv = (agent: AgentId, kind: Move["kind"], callsigns: string[], params: Move["params"], label: string): Move =>
  ({ id: `m${seq}`, agent, kind, callsigns, params, label });
const vote = (plan_id: string, p: number, utility: number, reservation: number, reason: string) =>
  ({ plan_id, decision: p >= 0.5 ? "ACCEPT" as const : "REJECT" as const, p_accept: p, utility, reservation, reason });

msg(0, "DECIDER", { type: "TRIAGE", score: 0.74, levels: ["low", "moderate", "high", "severe"], probabilities: [0.04, 0.19, 0.58, 0.19], confidence: 0.58 },
  "Thunderstorm, wet runways, 3 of 4 flights delayed more than 15 min, one heavy with a crew limit",
  { kind: "score", model: JEV, provider: "typesafe", latency_ms: 612, probabilities: { low: 0.04, moderate: 0.19, high: 0.58, severe: 0.19 }, confidence: 0.58 });
msg(0, "ATC", { type: "OPEN", plan: P0, evaluation: E0 }, "First free slot per runway in ETA order, scheduled gates", { kind: "formula", model: "formula", provider: "formula", latency_ms: 3 });

// round 1
msg(1, "ATC", { type: "MOVE_REJECTED", move: mv("ATC", "shift_slot", ["DL72"], { minutes: -4 }, "Move DL72 to T+25 on 26R"),
  violations: violation([d("DL1172", "26R", 19, "A12"), d("DL2384", "27L", 26, "A14"), d("DL72", "26R", 25, "E5"), d("DL1611", "28", 31, "A16")], "PLT-EARLIEST-ETA", "DL72") },
  "Pull the heavy ahead of WN1188 to cut its hold", choice(688, { shift_slot: 0.46, change_runway: 0.39, swap_order: 0.15 }));
msg(1, "ATC", { type: "MOVE", move: mv("ATC", "change_runway", ["DL72"], { runway: "28", slot_min: 27 }, "DL72 to runway 28 at T+27"), plan: P1a, options_count: 7 },
  "28 has a gap after DL398 and the shortest taxi to concourse E", choice(702, { change_runway: 0.71, swap_order: 0.21, shift_slot: 0.08 }));
msg(1, "PILOT:DL1172", { type: "MOVE", move: mv("PILOT:DL1172", "accept", ["DL1172"], {}, "Accept T+19 on 26R"), plan: P1a, options_count: 3 },
  "Already at earliest ETA; the gate is the problem, not the slot", choice(655, { accept: 0.82, speed_reduction: 0.12, request_divert: 0.06 }));
msg(1, "PILOT:DL2384", { type: "MOVE", move: mv("PILOT:DL2384", "accept", ["DL2384"], {}, "Accept T+26 on 27L"), plan: P1a, options_count: 3 },
  "2 min hold is within 18 min extra fuel", choice(640, { accept: 0.88, speed_reduction: 0.12 }));
msg(1, "PILOT:DL72", { type: "MOVE", move: mv("PILOT:DL72", "accept", ["DL72"], {}, "Accept T+27 on 28"), plan: P1a, options_count: 3 },
  "No hold on 28; crew limit T+50 still at risk at gate E5", choice(671, { accept: 0.77, speed_reduction: 0.18, request_divert: 0.05 }));
msg(1, "PILOT:DL1611", { type: "MOVE", move: mv("PILOT:DL1611", "accept", ["DL1611"], {}, "Accept T+31 on 28, no ground delay"), plan: P1a, options_count: 4 },
  "On the ground at TYS; T+31 needs no ground delay", choice(598, { accept: 0.74, ground_delay: 0.26 }));
msg(1, "GROUND", { type: "MODEL_FALLBACK", agent: "GROUND", from_model: MODEL, to_model: JEV, error: "timeout after 2000 ms" },
  "Primary decision model timed out; next in the fallback chain answered");
msg(1, "GROUND", { type: "MOVE_REJECTED", move: mv("GROUND", "reassign_gate", ["DL72"], { gate: "A14" }, "DL72 to gate A14"),
  violations: violation([d("DL1172", "26R", 19, "A12"), d("DL2384", "27L", 26, "RS"), d("DL72", "28", 27, "A14"), d("DL1611", "28", 31, "A16")], "GND-GATE-SIZE", "DL72") },
  "A14 is free from T+0", { kind: "choice", model: JEV, provider: "typesafe", latency_ms: 734, fallback_from: [MODEL], probabilities: { reassign_gate: 0.52, remote_stand: 0.31, swap_gates: 0.17 } });
msg(1, "GROUND", { type: "MOVE", move: mv("GROUND", "reassign_gate", ["DL72"], { gate: "E7" }, "DL72 to gate E7"), plan: plan("p1-gnd-a", [d("DL1172", "26R", 19, "A12"), d("DL2384", "27L", 26, "A14"), d("DL72", "28", 27, "E7"), d("DL1611", "28", 31, "A16")], wave), options_count: 9 },
  "E7 is free from T+22, wide-body, and keeps the crew inside its limit", { kind: "choice", model: JEV, provider: "typesafe", latency_ms: 701, fallback_from: [MODEL], probabilities: { reassign_gate: 0.68, swap_gates: 0.2, remote_stand: 0.12 } });
msg(1, "GROUND", { type: "MOVE", move: mv("GROUND", "remote_stand", ["DL1172"], { gate: "RS" }, "DL1172 to the remote stand"), plan: P1g, options_count: 8 },
  "A12 is held by the late DL1490 until T+41", { kind: "choice", model: JEV, provider: "typesafe", latency_ms: 689, fallback_from: [MODEL], probabilities: { remote_stand: 0.49, swap_gates: 0.44, reassign_gate: 0.07 } });
msg(1, "ATC", { type: "COUNTER", plan: P1a, evaluation: E1a }, "DL72 on 28");
msg(1, "GROUND", { type: "COUNTER", plan: P1g, evaluation: E1g }, "DL72 at E7, DL1172 bussed from the remote stand");
msg(1, "ENGINE", { type: "CHECK", plan_id: P1a.id, passed: E1a.feasible, violations: E1a.checks.filter((c) => !c.passed) }, "Vetoed: DL72 still waits for E5, crew past its limit");
msg(1, "ENGINE", { type: "CHECK", plan_id: P1g.id, passed: E1g.feasible, violations: E1g.checks.filter((c) => !c.passed) }, "All 36 checks on 4 flights");
const noul = (latency_ms: number, p: number, extra: Partial<DecisionMeta> = {}): DecisionMeta =>
  ({ kind: "noul", model: MODEL, provider: "openrouter", latency_ms, confidence: p, ...extra });
msg(1, "ATC", { type: "VOTE", votes: [vote(P1g.id, 0.79, 0.69, 0.6, "Clears the 26R hold")] }, "Keeps separation with margin", noul(633, 0.8));
msg(1, "PILOT:DL1172", { type: "VOTE", votes: [vote(P1g.id, 0.33, 0.44, 0.6, "Bussing adds 12 min, 9 pax miss DL905")] }, "Does not protect the BOS connection", noul(658, 0.33));
msg(1, "PILOT:DL2384", { type: "VOTE", votes: [vote(P1g.id, 0.86, 0.72, 0.6, "Unchanged")] }, "My flight is not touched", noul(611, 0.86));
msg(1, "PILOT:DL72", { type: "VOTE", votes: [vote(P1g.id, 0.9, 0.81, 0.6, "E7 at T+33, crew inside limit")] }, "Gate E7 fixes the crew limit", noul(677, 0.9));
msg(1, "PILOT:DL1611", { type: "VOTE", votes: [vote(P1g.id, 0.78, 0.66, 0.6, "Unchanged")] }, "My flight is not touched", noul(590, 0.78));
msg(1, "GROUND", { type: "VOTE", votes: [vote(P1g.id, 0.71, 0.62, 0.6, "Uses the remote stand once")] }, "Accepts its own counter", { ...noul(712, 0.71), model: JEV, provider: "typesafe", fallback_from: [MODEL] });
msg(1, "ENGINE", { type: "NO_CONSENSUS", valid_plan_ids: [P1g.id], next_reservation: u(0.55, 0.55, 0.55, 0.5, 0.55, 0.55) },
  "PILOT:DL1172 rejects the only valid plan; reservations drop for round 2");

// round 2
msg(2, "ATC", { type: "MOVE", move: mv("ATC", "accept", [], {}, "Keep the runway plan"), plan: P1g, options_count: 6 }, "No runway move improves the table plan", choice(645, { accept: 0.7, swap_order: 0.3 }));
msg(2, "GROUND", { type: "MOVE", move: mv("GROUND", "swap_gates", ["DL1172", "DL2384"], { gates: "A14 ↔ A12" }, "Swap gates DL1172 ↔ DL2384"), plan: P2, options_count: 8 },
  "DL1172 to A14 protects 23 connecting pax; DL2384 takes A12 after DL1490 leaves", choice(721, { swap_gates: 0.64, remote_stand: 0.28, reassign_gate: 0.08 }));
msg(2, "GROUND", { type: "COUNTER", plan: P2, evaluation: E2 }, "Swap A12/A14, DL72 at E7, no remote stand");
msg(2, "ENGINE", { type: "CHECK", plan_id: P2.id, passed: E2.feasible, violations: E2.checks.filter((c) => !c.passed) }, "All 36 checks on 4 flights");
msg(2, "ATC", { type: "VOTE", votes: [vote(P2.id, 0.83, 0.69, 0.55, "Runway plan unchanged")] }, "Accept", noul(602, 0.83));
msg(2, "PILOT:DL1172", { type: "VOTE", votes: [vote(P2.id, 0.93, 0.84, 0.55, "In-block T+26, both connections make it")] }, "Accept", noul(631, 0.93));
msg(2, "PILOT:DL2384", { type: "VOTE", votes: [vote(P2.id, 0.52, 0.52, 0.5, "In-block T+42 instead of T+33; 6 pax miss DL1188")] }, "Accepts narrowly after conceding", noul(668, 0.52));
msg(2, "PILOT:DL72", { type: "VOTE", votes: [vote(P2.id, 0.91, 0.81, 0.55, "E7, crew inside limit")] }, "Accept", noul(640, 0.91));
msg(2, "PILOT:DL1611", { type: "VOTE", votes: [vote(P2.id, 0.79, 0.66, 0.55, "Unchanged")] }, "Accept", noul(587, 0.79));
msg(2, "GROUND", { type: "VOTE", votes: [vote(P2.id, 0.84, 0.71, 0.55, "No bussing, no crew breach")] }, "Accept", noul(655, 0.84));
msg(2, "ENGINE", { type: "CONSENSUS", plan_id: P2.id, accepted_by: Object.keys(E2.utilities) as AgentId[] }, "Accepted by all 6 agents; highest minimum utility among accepted plans");

export const mockLog = log;

const delayGain = E0.totals.delay_min - E2.totals.delay_min;
export const mockResult: NegotiationResult = {
  run_id: "replay-ATL-2024-07-15-16", wave_id: wave.id, mode: "decision+claude", model: MODEL,
  outcome: "consensus", rounds: 2, final_plan: P2, evaluation: E2, baseline: { plan: P0, evaluation: E0 },
  narration: {
    source: "template",
    brief: `Agents agreed in 2 rounds. DL72 moves to runway 28 and gate E7, which keeps its crew inside the duty limit the status quo breaks. DL1172 and DL2384 swap gates so DL1172's 23 connecting passengers make their flights; the cost is DL2384 reaching the gate 9 min later and 6 passengers missing DL1188 to MSY. Total gate delay falls by ${delayGain} min and holding fuel by ${E0.totals.fuel_kg - E2.totals.fuel_kg} kg.`,
    lines: [],
  },
  model_errors: ["GROUND: liquid/d1-ultra timeout after 2000 ms (fell back to typesafe/jev-1.14)"],
  stats: { model_calls: log.filter((m) => m.decision && m.decision.provider !== "formula").length, fallbacks: 1, elapsed_ms: 18420 },
  log,
};

export const mockConfig: Config = {
  version: "offline-demo", modes: ["formulas", "decision", "decision+claude"], default_mode: "decision+claude",
  models: [
    { id: MODEL, provider: "openrouter", label: "LiquidAI d1-ultra", available: true, benchmark_rank: 1 },
    { id: JEV, provider: "typesafe", label: "Jev 1.14", available: true, benchmark_rank: 2 },
    { id: "formula", provider: "formula", label: "Formulas", available: true },
  ],
  default_model: MODEL, llm_available: false, tracing: false, replay_available: true,
};

export const mockWaves: WaveSummary[] = [{
  id: wave.id, airport: { iata: "ATL", icao: "KATL", name: wave.airport.name }, window_start: T0, window_end: iso(60),
  n_flights: wave.flights.length, n_queue: wave.queue.length, callsigns: wave.flights.map((f) => f.callsign),
  disrupted_callsigns: wave.flights.filter((f) => f.disrupted).map((f) => f.callsign), weather_summary: "25018G26KT 3SM +TSRA, wet", replay_available: true,
}];

export const mockBenchmark: BenchmarkReport | null = null;

// figures that must agree
console.assert(E2.feasible && !E0.feasible, "final plan passes, status quo breaks crew duty");
console.assert(!E1a.feasible, "ATC counter is vetoed for crew duty");
console.assert(E2.checks.length === 36, "9 rules x 4 flights");
