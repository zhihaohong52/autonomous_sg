// A small copy of the backend's rules for the offline demo wave. It computes every metric, rule
// check and total from the assignments, so the mock never shows a figure that does not add up.
import type {
  Assignment, FlightMetrics, Gate, JointPlan, PlanEvaluation, QueueArrival, RuleCheck, RuleId,
  WaveFlight, WaveScenario, Wake, AgentId,
} from "../api/types";

export const MCT_MIN = 35;                         // minimum connection time
const TURN_MIN = { narrow: 40, wide: 90 } as const;
const WET_FACTOR = 1.2;
const BURN_KG_MIN: Record<string, number> = { A321: 42, B739: 44, A333: 98, CRJ9: 26 };
// taxi minutes from runway to concourse (A or E); RS = remote stand
const TAXI: Record<string, Record<string, number>> = {
  "26R": { A: 7, E: 10, R: 9 }, "27L": { A: 7, E: 9, R: 8 }, "28": { A: 6, E: 6, R: 5 },
};

export type Draft = Omit<Assignment, "metrics">;

const concourse = (gate: string) => (gate === "RS" ? "R" : gate[0]);
const sepMin = (lead: Wake, follow: Wake) =>
  ((lead === "HEAVY" || lead === "SUPER") && follow !== "HEAVY" && follow !== "SUPER" ? 2.5 : 1.5) * WET_FACTOR;

export function metricsFor(f: WaveFlight, a: Draft, gates: Gate[]): FlightMetrics {
  const slot = a.slot_min ?? f.eta_earliest_min;
  const gate = gates.find((g) => g.id === a.gate);
  const busyUntil = Math.max(0, ...(gate?.busy.filter((b) => b.from_min <= slot + 15).map((b) => b.to_min) ?? []));
  const taxi = TAXI[a.runway ?? "26R"][concourse(a.gate ?? "A")];
  const bussing = a.gate === "RS" ? 12 : 0;
  const inBlock = Math.max(slot + taxi, busyUntil + 1) + bussing;
  const hold = Math.max(0, slot - f.eta_earliest_min - a.speed_reduction_min - a.ground_delay_min);
  const paxTotal = f.connections.reduce((s, c) => s + c.pax, 0);
  const paxProtected = f.connections.filter((c) => inBlock + MCT_MIN <= c.departs_min).reduce((s, c) => s + c.pax, 0);
  return {
    hold_min: hold,
    fuel_kg: hold * (BURN_KG_MIN[f.aircraft_type ?? ""] ?? 40),
    delay_min: inBlock - f.sched_min,
    in_block_min: inBlock,
    pax_protected: paxProtected,
    pax_total: paxTotal,
    overtime_min: Math.max(0, inBlock + 15 - f.crew_shift_end_min),
    next_dep_delay_min: Math.max(0, inBlock + TURN_MIN[f.gate_size] - f.next_departure_min),
  };
}

export function plan(id: string, drafts: Draft[], w: WaveScenario): JointPlan {
  const assignments = drafts.map((d) => ({ ...d, metrics: metricsFor(w.flights.find((f) => f.callsign === d.callsign)!, d, w.gates) }));
  const label = assignments.map((a) => `${a.callsign} R${a.runway} T+${a.slot_min} ${a.gate}`).join(" · ");
  return { id, label, assignments };
}

function occupancy(p: JointPlan, w: WaveScenario, except: string) {
  return p.assignments.filter((a) => a.callsign !== except).map((a) => {
    const f = w.flights.find((x) => x.callsign === a.callsign)!;
    const from = a.metrics.in_block_min ?? 0;
    return { gate: a.gate, from, to: Math.max(f.next_departure_min, from + TURN_MIN[f.gate_size]), label: a.callsign };
  });
}

export function checksFor(p: JointPlan, w: WaveScenario): RuleCheck[] {
  const out: RuleCheck[] = [];
  const metar = w.metar?.raw ?? "";
  const traffic = (rwy: string, except: string): QueueArrival[] => [
    ...w.queue.filter((q) => q.runway === rwy),
    ...p.assignments.filter((a) => a.runway === rwy && a.callsign !== except).map((a) => {
      const f = w.flights.find((x) => x.callsign === a.callsign)!;
      return { callsign: a.callsign, aircraft_type: f.aircraft_type, wake: f.wake, runway: rwy, slot_min: a.slot_min! };
    }),
  ];
  for (const a of p.assignments) {
    const f = w.flights.find((x) => x.callsign === a.callsign)!;
    const m = a.metrics;
    const slot = a.slot_min!;
    const add = (rule: RuleId, owner: RuleCheck["owner"], passed: boolean, detail: string, source: string, evidence: RuleCheck["evidence"]) =>
      out.push({ rule, owner, callsign: f.callsign, passed, detail, source, evidence });

    const rwy = w.airport.runways.find((r) => r.id === a.runway)!;
    add("ATC-RWY-OPEN", "ATC", rwy.open, rwy.open ? `Runway ${rwy.id} open` : `Runway ${rwy.id} closed: ${rwy.closed_reason}`,
      "Airport state (simulated)", { runway: rwy.id });

    const others = traffic(a.runway!, f.callsign);
    const lead = others.filter((o) => o.slot_min <= slot).sort((x, y) => y.slot_min - x.slot_min)[0];
    const next = others.filter((o) => o.slot_min > slot).sort((x, y) => x.slot_min - y.slot_min)[0];
    const needLead = lead ? sepMin(lead.wake, f.wake) : 0;
    const needNext = next ? sepMin(f.wake, next.wake) : 0;
    const okLead = !lead || slot - lead.slot_min >= needLead;
    const okNext = !next || next.slot_min - slot >= needNext;
    add("ATC-WAKE-SEP", "ATC", okLead && okNext,
      lead ? `${slot - lead.slot_min} min behind ${lead.callsign} (${lead.wake} → ${f.wake}), minimum ${needLead.toFixed(1)} min wet` : "First arrival on the runway in the window",
      "ICAO wake separation (time-based, ×1.2 wet)",
      { leader: lead?.callsign ?? "—", gap_min: lead ? slot - lead.slot_min : "—", min_gap_min: needLead.toFixed(1) });

    add("ATC-TAXI-ROUTE", "ATC", true, `Route ${a.runway} → ${a.gate} open, ${TAXI[a.runway!][concourse(a.gate!)]} min taxi`,
      "Taxiway state (simulated)", { from: a.runway!, to: a.gate! });

    const rw = w.runway_wind.find((x) => x.runway === a.runway)!;
    add("ATC-WIND-LIMITS", "ATC", rw.within_limits,
      `Crosswind ${rw.crosswind_kt} kt ≤ ${rw.limit_crosswind_kt} kt, tailwind ${rw.tailwind_kt} kt ≤ ${rw.limit_tailwind_kt} kt`,
      rw.limit_source, { metar, crosswind_kt: rw.crosswind_kt, limit_kt: rw.limit_crosswind_kt });

    add("PLT-EARLIEST-ETA", "PILOT", slot >= f.eta_earliest_min,
      `Slot T+${slot} ${slot >= f.eta_earliest_min ? "≥" : "<"} earliest ETA T+${f.eta_earliest_min}`, "Flight plan ETA", { slot_min: slot, eta_min: f.eta_earliest_min });

    add("PLT-FUEL-RESERVE", "PILOT", m.hold_min <= f.fuel.extra_min,
      `Hold ${m.hold_min} min ≤ ${f.fuel.extra_min} min extra fuel above alternate ${f.fuel.alternate} + ${f.fuel.final_reserve_min} min final reserve`,
      "Ops rule: alternate + 30 min final reserve", { hold_min: m.hold_min, extra_min: f.fuel.extra_min });

    const gate = w.gates.find((g) => g.id === a.gate)!;
    const sizeOk = gate.size === "wide" || f.gate_size === "narrow";
    add("GND-GATE-SIZE", "GROUND", sizeOk, `${f.aircraft_type} (${f.gate_size}-body) at ${gate.id} (${gate.size})`, "Gate table (OpenStreetMap + ops rule)", { gate: gate.id, gate_size: gate.size, aircraft: f.aircraft_type ?? "—" });

    const from = m.in_block_min ?? 0;
    const to = Math.max(f.next_departure_min, from + TURN_MIN[f.gate_size]);
    const clash = [...gate.busy.map((b) => ({ from: b.from_min, to: b.to_min, label: b.label })),
      ...occupancy(p, w, f.callsign).filter((o) => o.gate === gate.id)].find((b) => b.from < to && from < b.to);
    add("GND-GATE-FREE", "GROUND", gate.remote || !clash,
      clash && !gate.remote ? `${gate.id} taken by ${clash.label} T+${clash.from}–${clash.to}` : `${gate.id} free T+${from}–${to}`,
      "Gate occupancy (simulated)", { gate: gate.id, from_min: from, to_min: to });

    add("GND-CREW-DUTY", "GROUND", m.overtime_min === 0,
      m.overtime_min === 0 ? `In-block T+${from} + 15 min ≤ shift end T+${f.crew_shift_end_min}` : `In-block T+${from} + 15 min exceeds shift end T+${f.crew_shift_end_min} by ${m.overtime_min} min`,
      "Ops rule: crew duty limit (simulated shift)", { in_block_min: from, shift_end_min: f.crew_shift_end_min });
  }
  return out;
}

export function evaluate(p: JointPlan, w: WaveScenario, utilities: Record<AgentId, number>): PlanEvaluation {
  const checks = checksFor(p, w);
  const sum = (k: keyof FlightMetrics) => p.assignments.reduce((s, a) => s + (a.metrics[k] ?? 0), 0);
  const pax = sum("pax_protected"), paxTotal = sum("pax_total");
  return {
    plan_id: p.id,
    feasible: checks.every((c) => c.passed),
    checks,
    utilities,
    totals: {
      delay_min: sum("delay_min"), fuel_kg: sum("fuel_kg"), hold_min: sum("hold_min"),
      pax_protected_pct: paxTotal ? Math.round((pax / paxTotal) * 1000) / 10 : 0,
      overtime_min: sum("overtime_min"), diversions: p.assignments.filter((a) => a.action === "divert").length,
    },
  };
}
