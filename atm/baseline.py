"""Status quo baseline: no cross-domain coordination (phone-call world).

ATC gives the first wake-legal slot on the first open runway (first come, first served). The
aircraft taxis to its scheduled gate; if the gate is still occupied it waits on the apron
until the gate frees up. A blocked taxiway costs an ad-hoc reroute. If the hold eats the
extra fuel, the crew diverts. Nobody checks crew duty limits up front.
"""
from __future__ import annotations

from . import plans as P
from .scenario import TURNAROUND_MIN, Scenario

REROUTE_MIN = 8


def run(sc: Scenario) -> dict:
    rwy = next(r for r, v in sc.runways.items() if v["open"])
    slot = next((p for p in P.generate(sc, horizon=120)
                 if p.kind == "land" and p.runway == rwy and p.gate == sc.scheduled_gate
                 and all(c.passed for c in P.constraints(sc, p) if c.rule == "ATC-WAKE-SEP")), None)
    if slot is None or slot.time - sc.eta_earliest > sc.fuel["extra_min"]:
        m = P.metrics(sc, P.Plan("DIVERT", "divert"))
        return {"plan": "Divert (holding exceeded extra fuel)", "diverted": True, **m, "gate_wait_min": 0,
                "crew_duty_breach": False}
    m = P.metrics(sc, slot)
    gate = next(g for g in sc.gates if g.id == sc.scheduled_gate)
    in_block = m["in_block"]
    bt = sc.blocked_taxiway
    if bt and bt["cuts"] == [slot.runway, gate.pier]:
        in_block += REROUTE_MIN
    wait = 0.0
    for a, b in sorted(gate.busy):
        if a < in_block + TURNAROUND_MIN[sc.gate_size] and in_block + wait < b:
            wait = max(wait, b - in_block)
    in_block += wait
    turn_end = in_block + TURNAROUND_MIN[sc.gate_size]
    protected = sum(c.pax for c in sc.connections if in_block + P.MIN_CONNECT_MIN <= c.departs)
    return {"plan": f"FCFS {slot.runway} T{slot.time:+.0f}, scheduled gate {gate.id}", "diverted": False,
            "hold_min": m["hold_min"], "fuel_kg": m["fuel_kg"], "taxi_min": m["taxi_min"],
            "in_block": round(in_block, 1), "gate_wait_min": round(wait, 1),
            "delay_min": round(in_block - 5, 1), "turn_end": round(turn_end, 1),
            "overtime_min": round(max(0.0, turn_end - sc.crew_shift_end), 1),
            "crew_duty_breach": turn_end > sc.crew_shift_end + sc.crew_max_overtime,
            "pax_protected": protected, "pax_total": m["pax_total"],
            "next_dep_delay": round(max(0.0, turn_end - sc.next_departure), 1)}
