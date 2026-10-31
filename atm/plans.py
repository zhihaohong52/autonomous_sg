"""Candidate resolution plans, the hard-constraint engine and the per-agent decision models.

Three layers, deliberately separate:
  * `constraints()`  - deterministic safety/legality rules. A violation vetoes a plan, no
                       matter what any agent (or LLM) wants.
  * `utility()`      - each agent's private decision model: a weighted score in [0, 1] of how
                       well a plan serves that agent's objectives, with the term breakdown.
  * negotiation.py   - who proposes what, and when agreement is reached.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .scenario import FINAL_RESERVE_MIN, TAXI_MIN, TURNAROUND_MIN, WAKE_SEP_MIN, Scenario

AGENTS = ("ATC", "PILOT", "GROUND")
SPEED_CONTROL_MIN = 6  # delay absorbable by slowing down en route at ~20% of holding burn
MIN_CONNECT_MIN = 30
REMOTE_BUS_MIN = 10  # extra time to bus passengers from the remote stand


@dataclass(frozen=True)
class Plan:
    id: str
    kind: str  # land | divert
    runway: str | None = None
    time: float | None = None  # landing time, min rel. to scheduled arrival
    gate: str | None = None

    def label(self) -> str:
        if self.kind == "divert":
            return "Divert to alternate"
        return f"Land {self.runway} at T{self.time:+.0f}, gate {self.gate}"


@dataclass
class Check:
    rule: str
    owner: str
    passed: bool
    detail: str


def plan_id(runway: str, time: float, gate: str) -> str:
    return f"{runway}-{int(round(time))}-{gate}"


def generate(sc: Scenario, horizon: int = 60) -> list[Plan]:
    start = math.ceil(sc.eta_earliest)
    plans = [Plan(plan_id(r, t, g.id), "land", r, float(t), g.id)
             for r in sc.runways for t in range(start, start + horizon + 1) for g in sc.gates]
    return plans + [Plan("DIVERT", "divert")]


def parse_plan(sc: Scenario, pid: str) -> Plan | None:
    """Rebuild a plan from its id (used to validate LLM output - unknown ids are rejected)."""
    if pid == "DIVERT":
        return Plan("DIVERT", "divert")
    try:
        r, t, g = pid.split("-")
        t = float(t)
    except ValueError:
        return None
    if r not in sc.runways or g not in {x.id for x in sc.gates}:
        return None
    return Plan(pid, "land", r, t, g)


# ---------------------------------------------------------------- derived quantities

def _gate(sc: Scenario, gid: str):
    return next(g for g in sc.gates if g.id == gid)


def metrics(sc: Scenario, p: Plan) -> dict:
    """Physical consequences of a plan. Shared by constraints, utilities, baseline and eval."""
    total_pax = sum(c.pax for c in sc.connections)
    if p.kind == "divert":
        return {"hold_min": 0.0, "fuel_kg": round(sc.fuel["alternate_min"] * sc.fuel["burn_kg_min"] * 1.1),
                "taxi_min": None, "in_block": None, "delay_min": 240.0, "turn_end": None,
                "overtime_min": 0.0, "pax_protected": 0, "pax_total": total_pax, "next_dep_delay": 240.0}
    hold = max(0.0, p.time - sc.eta_earliest)
    burn = sc.fuel["burn_kg_min"]
    fuel = min(hold, SPEED_CONTROL_MIN) * burn * 0.2 + max(0.0, hold - SPEED_CONTROL_MIN) * burn
    gate = _gate(sc, p.gate)
    near = gate.pier != "R" and (p.runway == "R1") == (gate.pier == "A")
    taxi = TAXI_MIN["near" if near else "far"]
    in_block = p.time + taxi
    turn_end = in_block + TURNAROUND_MIN[sc.gate_size]
    pax_ready = in_block + (REMOTE_BUS_MIN if gate.pier == "R" else 0)
    protected = sum(c.pax for c in sc.connections if pax_ready + MIN_CONNECT_MIN <= c.departs)
    return {"hold_min": round(hold, 1), "fuel_kg": round(fuel), "taxi_min": taxi,
            "in_block": round(in_block, 1), "gate_wait_min": 0.0, "delay_min": round(in_block - TAXI_MIN["near"], 1),
            "turn_end": round(turn_end, 1), "overtime_min": round(max(0.0, turn_end - sc.crew_shift_end), 1),
            "pax_protected": protected, "pax_total": total_pax,
            "next_dep_delay": round(max(0.0, turn_end - sc.next_departure), 1)}


# ---------------------------------------------------------------- hard constraints

def constraints(sc: Scenario, p: Plan) -> list[Check]:
    if p.kind == "divert":
        ok = sc.fuel["extra_min"] >= 0
        return [Check("PLT-FUEL-RESERVE", "PILOT", ok,
                      f"Divert to {sc.fuel['alternate']} ({sc.fuel['alternate_min']} min) covered by alternate fuel")]
    m = metrics(sc, p)
    out = []
    rw = sc.runways[p.runway]
    out.append(Check("ATC-RWY-OPEN", "ATC", rw["open"], f"Runway {p.runway}: {rw['reason']}"))

    sep_f = sc.weather["separation_factor"]
    same = sorted((a for a in sc.arrivals if a.runway == p.runway), key=lambda a: a.time)
    lead = max((a for a in same if a.time <= p.time), key=lambda a: a.time, default=None)
    foll = min((a for a in same if a.time > p.time), key=lambda a: a.time, default=None)
    ok, detail = True, "No conflicting traffic"
    if lead:
        need = WAKE_SEP_MIN[(lead.wake, sc.wake)] * sep_f
        if p.time - lead.time < need:
            ok, detail = False, f"{p.time - lead.time:.1f} min behind {lead.callsign} ({lead.wake}), need {need:.1f}"
    if ok and foll:
        need = WAKE_SEP_MIN[(sc.wake, foll.wake)] * sep_f
        if foll.time - p.time < need:
            ok, detail = False, f"{foll.time - p.time:.1f} min ahead of {foll.callsign} ({foll.wake}), need {need:.1f}"
    if ok and (lead or foll):
        detail = "Wake separation met" + (f" behind {lead.callsign}" if lead else "") + (f", ahead of {foll.callsign}" if foll else "")
    out.append(Check("ATC-WAKE-SEP", "ATC", ok, detail + f" (weather factor x{sep_f})"))

    gate = _gate(sc, p.gate)
    bt = sc.blocked_taxiway
    cut = bt is not None and bt["cuts"] == [p.runway, gate.pier]
    out.append(Check("ATC-TAXI-ROUTE", "ATC", not cut,
                     f"Taxiway {bt['id']} closed ({bt['reason']}): no route {p.runway} -> pier {gate.pier}" if cut
                     else f"Taxi route {p.runway} -> pier {gate.pier} available ({m['taxi_min']} min)"))

    out.append(Check("PLT-EARLIEST-ETA", "PILOT", p.time >= sc.eta_earliest,
                     f"Earliest achievable landing T{sc.eta_earliest:+.1f}"))
    fuel_ok = m["hold_min"] <= sc.fuel["extra_min"]
    out.append(Check("PLT-FUEL-RESERVE", "PILOT", fuel_ok,
                     f"Hold {m['hold_min']:.0f} min vs {sc.fuel['extra_min']:.0f} min extra fuel above "
                     f"alternate ({sc.fuel['alternate']}) + {FINAL_RESERVE_MIN} min final reserve"))

    size_ok = gate.size == "wide" or sc.gate_size == "narrow"
    out.append(Check("GND-GATE-SIZE", "GROUND", size_ok,
                     f"Gate {gate.id} ({gate.size}-body) {'fits' if size_ok else 'cannot take'} a {sc.gate_size}-body aircraft"))
    occ = [(a, b) for a, b in gate.busy if a < m["turn_end"] and m["in_block"] < b]
    out.append(Check("GND-GATE-FREE", "GROUND", not occ,
                     f"Gate {gate.id} occupied T{occ[0][0]:+.0f}..T{occ[0][1]:+.0f}, need T{m['in_block']:+.0f}..T{m['turn_end']:+.0f}"
                     if occ else f"Gate {gate.id} free T{m['in_block']:+.0f}..T{m['turn_end']:+.0f}"))
    crew_ok = m["turn_end"] <= sc.crew_shift_end + sc.crew_max_overtime
    out.append(Check("GND-CREW-DUTY", "GROUND", crew_ok,
                     f"Turnaround ends T{m['turn_end']:+.0f}; crew shift ends T{sc.crew_shift_end:+.0f} "
                     f"(max {sc.crew_max_overtime} min overtime)"))
    return out


def feasible(sc: Scenario, p: Plan, owner: str | None = None) -> bool:
    return all(c.passed for c in constraints(sc, p) if owner is None or c.owner == owner)


# ---------------------------------------------------------------- decision models

def utility(sc: Scenario, p: Plan, agent: str) -> tuple[float, dict]:
    """Agent's private decision model. Returns (score in [0,1], weighted term breakdown)."""
    m = metrics(sc, p)
    if p.kind == "divert":
        base = {"ATC": 0.55, "PILOT": 0.15, "GROUND": 0.10}[agent]
        return base, {"diversion": base}
    if agent == "ATC":
        load = sum(1 for a in sc.arrivals if a.runway == p.runway and abs(a.time - p.time) <= 15) / 12
        terms = {"airspace_holding": -0.45 * min(m["hold_min"] / 40, 1),
                 "runway_load": -0.25 * min(load, 1),
                 "taxi_conflict": -0.15 * (m["taxi_min"] - TAXI_MIN["near"]) / 6}
    elif agent == "PILOT":
        extra = max(sc.fuel["extra_min"], 1)
        terms = {"fuel_margin": -0.35 * min(m["hold_min"] / extra, 1),
                 "delay": -0.25 * min(m["delay_min"] / 60, 1),
                 "connections": -0.30 * (1 - m["pax_protected"] / max(m["pax_total"], 1)),
                 "turbulence_in_hold": -0.10 * sc.weather["severity"] * min(m["hold_min"] / 20, 1)}
    else:
        gate = _gate(sc, p.gate)
        sched_pier = _gate(sc, sc.scheduled_gate).pier
        reassign = 0 if gate.id == sc.scheduled_gate else (0.4 if gate.pier == sched_pier else 0.6)
        if gate.pier == "R":
            reassign = 1.0
        terms = {"crew_overtime": -0.35 * min(m["overtime_min"] / sc.crew_max_overtime, 1),
                 "turnaround_delay": -0.35 * min(m["next_dep_delay"] / 60, 1),
                 "gate_reassignment": -0.20 * reassign,
                 "tow_and_taxi": -0.10 * (m["taxi_min"] - TAXI_MIN["near"]) / 6,
                 "remote_stand_bussing": -0.15 * (gate.pier == "R")}
    return round(max(0.0, 1 + sum(terms.values())), 3), {k: round(v, 3) for k, v in terms.items()}


def plan_card(sc: Scenario, p: Plan, with_checks: bool = True) -> dict:
    d = {"plan": asdict(p), "label": p.label(), "metrics": metrics(sc, p),
         "utility": {a: utility(sc, p, a)[0] for a in AGENTS}}
    if with_checks:
        cs = constraints(sc, p)
        d["checks"] = [asdict(c) for c in cs]
        d["feasible"] = all(c.passed for c in cs)
    return d
