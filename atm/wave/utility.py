"""Per-flight metrics, per-agent utility formulas and the contract PlanEvaluation for wave mode.

All times are minutes relative to the wave window start (T0). Weights follow v1 `atm/plans.py`,
extended to joint plans (ATC scores the whole wave, each Pilot its own flight, Ground gates/crew).
"""
from __future__ import annotations

import math

from atm.wave.model import Assignment, FlightMetrics, JointPlan, Wave, WaveFlight, agent_ids

# ---------------------------------------------------------------- physical constants
HOLD_BURN_KG_MIN = {"SUPER": 180.0, "HEAVY": 110.0, "B757": 70.0, "LARGE": 45.0, "SMALL": 25.0}
SPEED_REDUCTION_BURN_FRACTION = 0.20   # absorbing delay en route burns ~20% of holding fuel
TAXI_NEAR_MIN = 6.0
TAXI_FAR_MIN = 10.0
TYPICAL_TAXI_MIN = 6.0                 # scheduled in-block = sched_min + typical taxi
REMOTE_BUS_MIN = 10.0                  # passengers bussed from the remote stand
TURNAROUND_MIN = 45.0                  # gate occupancy and crew/aircraft turn
MIN_CONNECT_MIN = 35.0
DIVERT_EXTRA_DELAY_MIN = 90.0          # divert delay = alternate_min + 90
MAX_SPEED_REDUCTION_MIN = 8.0

# ---------------------------------------------------------------- utility normalisation caps
HOLD_CAP_MIN = 20.0          # ATC: mean hold per flight at which the holding penalty saturates
TAXI_EXTRA_CAP_MIN = TAXI_FAR_MIN - TAXI_NEAR_MIN   # ATC: extra taxi beyond near
DELAY_CAP_MIN = 60.0         # PILOT: own delay at which the delay penalty saturates
OVERTIME_CAP_MIN = 30.0      # GROUND: mean crew overtime per flight
NEXT_DEP_CAP_MIN = 60.0      # GROUND: mean next-departure delay per flight
DIVERT_FUEL_TERM = 0.5       # PILOT: fuel term for a diversion (fuel is safe but spent flying away)

W_ATC = {"holding": 0.45, "runway_load": 0.25, "taxi": 0.15, "diversions": 0.15}
W_PILOT = {"fuel": 0.35, "delay": 0.25, "connections": 0.30, "divert": 0.10}
W_GROUND = {"overtime": 0.35, "turnaround": 0.35, "reassignment": 0.20, "bussing": 0.10}


def fuel_info(f: WaveFlight) -> tuple[float, str | None, float | None, float]:
    """(extra_min, alternate, alternate_min, final_reserve_min) with missing values defaulted:
    extra 0, final reserve 30; alternate / alternate_min None when absent."""
    fuel = f.fuel or {}
    alt_min = fuel.get("alternate_min")
    return (float(fuel.get("extra_min") or 0), fuel.get("alternate") or None,
            float(alt_min) if alt_min is not None else None, float(fuel.get("final_reserve_min") or 30))


def hold_burn(f: WaveFlight) -> float:
    return HOLD_BURN_KG_MIN.get(f.wake, HOLD_BURN_KG_MIN["LARGE"])


def _gate(wave: Wave, gid: str | None):
    if gid is None:
        return None
    return next((g for g in wave.gates if g.id == gid), None)


def _group(items: list[str], item: str) -> int:
    """0 for the first half of `items`, 1 for the second half (-1 if unknown)."""
    if item not in items:
        return -1
    return (items.index(item) * 2) // max(len(items), 1)


def taxi_min(wave: Wave, runway: str | None, gate_id: str | None) -> float:
    """Near/far heuristic: the first half of the airport's runway list pairs with the first half of
    the (alphabetical) terminal piers. Remote stand is always far."""
    gate = _gate(wave, gate_id)
    if gate is None or runway is None or gate.remote:
        return TAXI_FAR_MIN
    rwys = [r.id for r in wave.airport.runways]
    piers = sorted({g.pier for g in wave.gates if not g.remote})
    near = _group(rwys, runway) == _group(piers, gate.pier) != -1
    return TAXI_NEAR_MIN if near else TAXI_FAR_MIN


def metrics(wave: Wave, a: Assignment) -> FlightMetrics:
    f = wave.flight(a.callsign)
    pax_total = sum(int(c.get("pax", 0)) for c in f.connections)
    burn = hold_burn(f)
    if a.action == "divert":
        alt = max(fuel_info(f)[2] or 0.0, 0.0)
        delay = alt + DIVERT_EXTRA_DELAY_MIN
        return FlightMetrics(hold_min=0.0, fuel_kg=round(alt * burn, 1), delay_min=round(delay, 1),
                             in_block_min=None, pax_protected=0, pax_total=pax_total,
                             overtime_min=0.0, next_dep_delay_min=round(delay, 1))
    # a missing / non-finite slot is scored as "at ETA" (PLT-EARLIEST-ETA rejects it separately)
    slot = float(a.slot_min) if isinstance(a.slot_min, (int, float)) and math.isfinite(a.slot_min) \
        else float(f.eta_earliest_min)
    hold = max(0.0, slot - f.eta_earliest_min - a.speed_reduction_min - a.ground_delay_min)
    fuel = hold * burn + a.speed_reduction_min * burn * SPEED_REDUCTION_BURN_FRACTION
    gate = _gate(wave, a.gate)
    in_block = slot + taxi_min(wave, a.runway, a.gate)
    pax_ready = in_block + (REMOTE_BUS_MIN if gate is not None and gate.remote else 0.0)
    protected = sum(int(c.get("pax", 0)) for c in f.connections
                    if float(c["departs_min"]) - pax_ready >= MIN_CONNECT_MIN)
    turn_end = in_block + TURNAROUND_MIN
    return FlightMetrics(
        hold_min=round(hold, 1), fuel_kg=round(fuel, 1),
        delay_min=round(max(0.0, in_block - (f.sched_min + TYPICAL_TAXI_MIN)), 1),
        in_block_min=round(in_block, 1), pax_protected=protected, pax_total=pax_total,
        overtime_min=round(max(0.0, turn_end - f.crew_shift_end_min), 1),
        next_dep_delay_min=round(max(0.0, turn_end - f.next_departure_min), 1))


def _clip(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _metrics_map(wave: Wave, plan: JointPlan) -> dict[str, FlightMetrics]:
    return {a.callsign: metrics(wave, a) for a in plan.assignments}


def agent_terms(wave: Wave, plan: JointPlan, agent_id: str,
                mm: dict[str, FlightMetrics] | None = None) -> dict[str, float]:
    """Weighted penalty terms (each <= 0) of an agent's utility. utility = 1 + sum(terms)."""
    mm = mm or _metrics_map(wave, plan)
    asg = plan.assignments
    n = max(len(asg), 1)
    if agent_id == "ATC":
        landing = [a for a in asg if a.action == "land"]
        mean_hold = sum(mm[a.callsign].hold_min for a in landing) / n
        counts: dict[str, int] = {}
        for a in landing:
            counts[a.runway or "?"] = counts.get(a.runway or "?", 0) + 1
        # concentration of wave landings on one runway: 0 when spread, 1 when all on one
        load = (max(counts.values()) - 1) / (len(landing) - 1) if len(landing) > 1 else 0.0
        taxi_extra = sum(taxi_min(wave, a.runway, a.gate) - TAXI_NEAR_MIN for a in landing) / n
        div = sum(1 for a in asg if a.action == "divert") / n
        return {"holding": -W_ATC["holding"] * _clip(mean_hold / HOLD_CAP_MIN),
                "runway_load": -W_ATC["runway_load"] * _clip(load),
                "taxi": -W_ATC["taxi"] * _clip(taxi_extra / TAXI_EXTRA_CAP_MIN),
                "diversions": -W_ATC["diversions"] * _clip(div)}
    if agent_id == "GROUND":
        over = sum(mm[a.callsign].overtime_min for a in asg) / n
        nxt = sum(mm[a.callsign].next_dep_delay_min for a in asg) / n
        reassign, bus = 0.0, 0.0
        for a in asg:
            if a.action != "land":
                continue
            f = wave.flight(a.callsign)
            g, sg = _gate(wave, a.gate), _gate(wave, f.scheduled_gate)
            if g is None or g.remote:
                reassign += 1.0
                bus += 1.0
            elif g.id != f.scheduled_gate:
                reassign += 0.4 if sg is not None and g.pier == sg.pier else 0.6
        return {"overtime": -W_GROUND["overtime"] * _clip(over / OVERTIME_CAP_MIN),
                "turnaround": -W_GROUND["turnaround"] * _clip(nxt / NEXT_DEP_CAP_MIN),
                "reassignment": -W_GROUND["reassignment"] * _clip(reassign / n),
                "bussing": -W_GROUND["bussing"] * _clip(bus / n)}
    if agent_id.startswith("PILOT:"):
        cs = agent_id.split(":", 1)[1]
        a = plan.get(cs)
        f = wave.flight(cs)
        m = mm[cs]
        if a.action == "divert":
            fuel_pen = DIVERT_FUEL_TERM
        else:
            budget = max(fuel_info(f)[0], 1.0) * hold_burn(f)
            fuel_pen = _clip(m.fuel_kg / budget)
        conn = 1 - m.pax_protected / m.pax_total if m.pax_total else 0.0
        return {"fuel": -W_PILOT["fuel"] * fuel_pen,
                "delay": -W_PILOT["delay"] * _clip(m.delay_min / DELAY_CAP_MIN),
                "connections": -W_PILOT["connections"] * _clip(conn),
                "divert": -W_PILOT["divert"] * (1.0 if a.action == "divert" else 0.0)}
    raise ValueError(f"unknown agent {agent_id!r}")


def agent_utility(wave: Wave, plan: JointPlan, agent_id: str,
                  mm: dict[str, FlightMetrics] | None = None) -> float:
    return round(_clip(1 + sum(agent_terms(wave, plan, agent_id, mm).values())), 3)


def evaluate(wave: Wave, plan: JointPlan) -> dict:
    """Contract PlanEvaluation. Fills each Assignment.metrics in place."""
    from atm.wave import rules  # lazy: rules imports metrics() from here

    try:
        rules.validate_structure(wave, plan)
    except ValueError:
        # Malformed plan (missing / unknown / duplicate callsigns): infeasible, nothing scored.
        return {"plan_id": plan.id, "feasible": False, "checks": [],
                "utilities": {ag: 0.0 for ag in agent_ids(wave)},
                "totals": {"delay_min": 0.0, "fuel_kg": 0.0, "hold_min": 0.0, "pax_protected_pct": 0.0,
                           "overtime_min": 0.0, "diversions": 0}}
    mm = _metrics_map(wave, plan)
    for a in plan.assignments:
        a.metrics = mm[a.callsign]
    checks = rules.check(wave, plan, mm)
    ms = list(mm.values())
    pax_total = sum(m.pax_total for m in ms)
    totals = {
        "delay_min": round(sum(m.delay_min for m in ms), 1),
        "fuel_kg": round(sum(m.fuel_kg for m in ms), 1),
        "hold_min": round(sum(m.hold_min for m in ms), 1),
        "pax_protected_pct": round(100.0 * sum(m.pax_protected for m in ms) / pax_total, 1) if pax_total else 100.0,
        "overtime_min": round(sum(m.overtime_min for m in ms), 1),
        "diversions": sum(1 for a in plan.assignments if a.action == "divert"),
    }
    return {"plan_id": plan.id,
            "feasible": all(c.passed for c in checks),
            "checks": [c.to_dict() for c in checks],
            "utilities": {ag: agent_utility(wave, plan, ag, mm) for ag in agent_ids(wave)},
            "totals": totals}
