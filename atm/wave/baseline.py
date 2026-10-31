"""Status-quo baseline: today's uncoordinated practice, reported honestly (it may violate rules)."""
from __future__ import annotations

from dataclasses import replace

from atm.wave import moves
from atm.wave.model import Assignment, FlightMetrics, JointPlan, Wave
from atm.wave.utility import metrics

LABEL = "Status quo (FCFS, scheduled gates, no coordination)"


def status_quo(wave: Wave) -> JointPlan:
    """FCFS by earliest ETA on the best-aligned open runway, scheduled gate even if busy, no speed
    reduction / ground delay / remote stand. A flight whose hold exceeds its extra fuel diverts."""
    plan = moves.fcfs_plan(wave)
    out = []
    for a in plan.assignments:
        f = wave.flight(a.callsign)
        if metrics(wave, a).hold_min > float(f.fuel.get("extra_min", 0)):
            a = Assignment(a.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics())
        else:
            a = replace(a, metrics=FlightMetrics())
        out.append(a)
    return JointPlan(out, LABEL)
