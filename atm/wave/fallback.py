"""Deterministic non-LLM fallback: beam search over legal moves maximising the worst agent's
utility, with a remote-stand / ground-delay / divert safety net. Legality is decided only by the
rule engine. solve() never raises."""
from __future__ import annotations

from dataclasses import replace

from atm.wave import moves, rules
from atm.wave.model import Assignment, FlightMetrics, JointPlan, Wave, agent_ids
from atm.wave.utility import agent_utility

MAX_DEPTH = 6


def _score(wave: Wave, plan: JointPlan, agents: list[str]) -> tuple:
    us = [agent_utility(wave, plan, ag) for ag in agents]
    nv = len(rules.violations(wave, plan))
    return (nv == 0, -nv, min(us), sum(us) / len(us))


def _divert(a: Assignment) -> Assignment:
    return Assignment(a.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics())


def _all_divert(wave: Wave, base: JointPlan) -> tuple[JointPlan, str]:
    plan = JointPlan([_divert(a) for a in base.assignments], "Fallback: safety net")
    try:
        ok = rules.feasible(wave, plan)
    except Exception:
        ok = False
    return plan, "safety net: all-divert" if ok else "safety net: all-divert (unverified)"


def _safety_net(wave: Wave) -> tuple[JointPlan, str]:
    start = moves.fcfs_plan(wave)
    try:
        plan = start
        for _ in range(4 * len(wave.flights) + 4):
            viol = rules.violations(wave, plan)
            bad = sorted({c.callsign for c in viol if c.callsign})
            if not bad:
                n_div = sum(1 for a in plan.assignments if a.action == "divert")
                if n_div == 0:
                    method = "safety net: remote stand/ground delay"
                elif n_div == len(plan.assignments):
                    method = "safety net: all-divert"
                else:
                    method = "safety net: partial divert"
                plan.label = "Fallback: safety net"
                return plan, method
            cs = bad[0]
            a, f = plan.get(cs), wave.flight(cs)
            nv = len(viol)
            rs = next((g.id for g in wave.gates if g.remote), None)
            gnd_fail = any(c.callsign == cs and c.rule.startswith("GND-") for c in viol)
            best = None
            if a.action == "land" and gnd_fail and rs is not None and a.gate != rs:
                best = plan.with_assignment(replace(a, gate=rs, metrics=FlightMetrics()))
            if best is None and a.action == "land" and not f.airborne:
                bn = nv
                for m in moves.legal_moves(wave, plan, f"PILOT:{cs}"):
                    if m.kind != "ground_delay":
                        continue
                    p2 = moves.apply(wave, plan, m)
                    n2 = len(rules.violations(wave, p2))
                    if n2 < bn:
                        bn, best = n2, p2
            if best is None:
                best = plan.with_assignment(_divert(a))
            plan = best
    except Exception:
        pass
    return _all_divert(wave, _fallback_base(wave))


def _fallback_base(wave: Wave) -> JointPlan:
    """Any plan with one assignment per flight (only callsigns are used by all-divert)."""
    return JointPlan([Assignment(f.callsign) for f in wave.flights])


def _beam(wave: Wave, budget: int, width: int) -> tuple[JointPlan, str] | None:
    agents = agent_ids(wave)
    start = moves.fcfs_plan(wave)
    seen = {start.id}
    n = 1
    s0 = _score(wave, start, agents)
    beam = [(s0, start)]
    best = ((s0[2], s0[3]), start.id, start) if s0[0] else None
    for _ in range(MAX_DEPTH):
        cand: list[tuple[tuple, JointPlan]] = []
        for _, plan in beam:
            for ag in agents:
                for m in moves.legal_moves(wave, plan, ag):
                    if m.kind == "accept":
                        continue
                    p2 = moves.apply(wave, plan, m)
                    if p2.id in seen:
                        continue
                    if n >= budget:
                        break
                    seen.add(p2.id)
                    n += 1
                    s = _score(wave, p2, agents)
                    cand.append((s, p2))
                    if s[0] and (best is None or (-s[2], -s[3], p2.id) < (-best[0][0], -best[0][1], best[1])):
                        best = ((s[2], s[3]), p2.id, p2)
                if n >= budget:
                    break
            if n >= budget:
                break
        if not cand:
            break
        cand.sort(key=lambda x: (not x[0][0], -x[0][1], -x[0][2], -x[0][3], x[1].id))
        beam = cand[:width]
        if n >= budget:
            break
    if best is None:
        return None
    plan = best[2]
    plan.label = "Fallback: beam search"
    note = "FCFS already best" if plan.id == start.id else "max-min utility"
    return plan, f"beam(width={width}, evaluated={n}): {note}"


def solve(wave: Wave, budget: int = 400, width: int = 12) -> tuple[JointPlan, str]:
    try:
        res = _beam(wave, budget, width)
    except Exception:
        res = None
    if res is not None:
        return res
    try:
        return _safety_net(wave)
    except Exception:
        return _all_divert(wave, _fallback_base(wave))
