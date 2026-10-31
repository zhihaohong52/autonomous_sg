"""Wave negotiation as a LangGraph state graph (spec §6, §8, §14; contract NegotiationMessage).

    triage -> open -> moves -> counter -> check -> vote -> decide_round -+-> finalize
                        ^                                                +-> concede -> moves
                        +------------------------------------------------+-> fallback -> finalize

Independent agents (ATC, one PILOT per wave flight, GROUND) each start from the round's table plan
and make up to 2 moves (3 when triage severity >= 3) inside their own authority (moves.legal_moves).
Every decision goes through a per-agent `Decider` (model chain -> local formula); the agent only
sees its private view. The deterministic rule engine (rules.check) is the only judge of legality.

Choices made here (documented for review):
- Move rejection: a move is rejected iff the plan after it has a violation (rule, callsign) that the
  plan before it did not have. When the table plan is feasible this is exactly
  `rules.feasible(after) is False`; when the table is already infeasible (common on real waves:
  FCFS often breaks fuel/gate rules) it still lets agents repair the plan step by step instead of
  every move being vetoed.
- Agent formula (Choice fallback / formulas mode): the agent ranks options by (number of violated
  rules it knows about after the move, -own utility). It knows the rules of its own domain plus every
  rule the engine has published so far (OPEN/CHECK violations, MOVE_REJECTED). It moves only if
  that strictly improves (fewer known violations, or same and higher own utility); otherwise a Pilot
  accepts and ATC/GROUND pass. Cross-domain rules it has not seen yet can still get a move vetoed.
- Vote formula: step function, P(accept) = 1.0 if utility >= reservation else 0.0 (exactly the
  "formula verdict" Brier is measured against in spec §14). ACCEPT iff P(accept) >= 0.5.
- Reservation_r = best own utility seen on a FEASIBLE plan so far (OPEN, counters) * (0.9 - 0.12 * (r - 1)).
  The round-3 NO_CONSENSUS reports the round-3 reservations (there is no round 4).
- Plan labels are the per-flight description (JointPlan.describe()); authorship is in sender/rationale.
- A slot allows at most MAX_REJECTIONS_PER_SLOT picks that the engine vetoes; then the agent's turn ends.
- Table plan for round r+1: the feasible counter-plan with the highest min-utility; if none is
  feasible, the counter with the fewest violations (ties: max-min utility); else unchanged.
- stats.model_calls = decisions answered by a model; stats.fallbacks = Decider fallback events.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atm.decide import DEFAULT_CHAIN, Decider, DecisionResult
from atm.wave import baseline, fallback, moves, rules, wind
from atm.wave.model import JointPlan, Move, Wave, agent_ids
from atm.wave.utility import agent_terms, agent_utility, evaluate, metrics

MAX_ROUNDS = 3
MOVES_PER_ROUND = 2
SEVERE_MOVES_PER_ROUND = 3
SEVERE_LEVEL = 3
MAX_REJECTIONS_PER_SLOT = 2
CONCESSION = 0.12
SEVERITY_LEVELS = ["0 minor: absorbable with normal sequencing",
                   "1 moderate: some holding or gate shuffles",
                   "2 significant: coordinated resequencing needed",
                   "3 severe: runway/wind limits or long delays, diversions possible",
                   "4 critical: most runways unusable or very long delays"]
OBJECTIVES = {
    "ATC": "Keep wake separation and wind limits, spread landings across runways, minimise airborne holding.",
    "PILOT": "Keep fuel reserves, minimise your own delay and holding, protect your passengers' connections.",
    "GROUND": "Avoid gate conflicts and crew overtime, keep scheduled gates, protect next departures.",
}
PASS = "pass"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role(agent: str) -> str:
    return "PILOT" if agent.startswith("PILOT:") else agent


def _plan_dict(wave: Wave, plan: JointPlan) -> dict:
    for a in plan.assignments:
        a.metrics = metrics(wave, a)
    return plan.to_dict()


def _vkeys(checks) -> set[tuple[str, str | None]]:
    return {(c.rule, c.callsign) for c in checks if not c.passed}


def _known_violations(agent: str, checks, known: set[str]) -> int:
    role = _role(agent)
    cs = agent.split(":", 1)[1] if role == "PILOT" else None
    n = 0
    for c in checks:
        if c.passed:
            continue
        own = c.owner == role and (cs is None or c.callsign == cs)
        if own or c.rule in known:
            n += 1
    return n


def _maxmin(wave: Wave, plan: JointPlan) -> tuple[float, float]:
    us = [agent_utility(wave, plan, ag) for ag in agent_ids(wave)]
    return min(us), sum(us) / len(us)


def _meta(kind: str, r: DecisionResult) -> dict:
    formula = r.provider == "formula"
    m: dict[str, Any] = {"kind": "formula" if formula else kind, "model": r.model, "provider": r.provider,
                         "latency_ms": int(r.latency_ms)}
    if not formula:
        if isinstance(r.probabilities, dict) and r.probabilities:
            m["probabilities"] = {str(k): float(v) for k, v in r.probabilities.items()
                                  if isinstance(v, (int, float))}
        if isinstance(r.confidence, (int, float)):
            m["confidence"] = float(r.confidence)
    if r.fallback_from:
        m["fallback_from"] = list(r.fallback_from)
    return m


# ---------------------------------------------------------------- private views (kept small)

def _view(wave: Wave, plan: JointPlan, agent: str) -> dict:
    role = _role(agent)
    if role == "ATC":
        m = wave.metar
        rw = []
        for r in wave.airport.runways:
            row = {"id": r.id, "open": r.open}
            if m is not None and r.open:
                hw, xw, tw = wind.components(m.wind_dir_deg, m.wind_kt, m.gust_kt, r.heading_true)
                lxw, ltw, _ = wind.limits(m.surface, None)
                row.update({"xwind_kt": xw, "tailwind_kt": tw, "xwind_limit_kt": lxw, "tail_limit_kt": ltw})
            rw.append(row)
        return {"metar": m.raw if m else None, "runways": rw,
                "queue": [[q.callsign, q.wake, q.runway, q.slot_min] for q in wave.queue][:20],
                "wave": [[a.callsign, wave.flight(a.callsign).wake, a.action, a.runway, a.slot_min]
                         for a in plan.assignments]}
    if role == "GROUND":
        mm = {a.callsign: metrics(wave, a) for a in plan.assignments}
        return {"gates": [[g.id, g.size, "remote" if g.remote else g.pier,
                           [[b["from_min"], b["to_min"]] for b in g.busy][:4]] for g in wave.gates][:24],
                "flights": [{"cs": a.callsign, "size": wave.flight(a.callsign).gate_size,
                             "gate": a.gate, "scheduled_gate": wave.flight(a.callsign).scheduled_gate,
                             "in_block": mm[a.callsign].in_block_min,
                             "crew_shift_end": wave.flight(a.callsign).crew_shift_end_min,
                             "next_dep": wave.flight(a.callsign).next_departure_min}
                            for a in plan.assignments]}
    cs = agent.split(":", 1)[1]
    f, a = wave.flight(cs), plan.get(cs)
    m = metrics(wave, a)
    return {"flight": cs, "type": f.aircraft_type, "airborne": f.airborne, "eta_earliest": f.eta_earliest_min,
            "sched": f.sched_min, "fuel": f.fuel, "connections": f.connections[:5],
            "assignment": {"action": a.action, "runway": a.runway, "slot": a.slot_min, "gate": a.gate,
                           "speed_reduction": a.speed_reduction_min, "ground_delay": a.ground_delay_min},
            "metrics": {"hold": m.hold_min, "fuel_kg": m.fuel_kg, "delay": m.delay_min,
                        "pax_protected": m.pax_protected, "pax_total": m.pax_total}}


# ---------------------------------------------------------------- run context

class _Run:
    def __init__(self, wave: Wave, mode: str, chain: list[str] | None,
                 on_message: Callable[[dict], None] | None, run_id: str):
        self.wave, self.mode, self.run_id = wave, mode, run_id
        self.chain = [] if mode == "formulas" else list(chain if chain is not None else DEFAULT_CHAIN)
        self.on_message = on_message
        self.log: list[dict] = []
        self.model_calls = 0
        self.fallbacks = 0
        self.model_errors: list[str] = []
        self.known_rules: set[str] = set()
        self.best_seen: dict[str, float] = {ag: 0.0 for ag in agent_ids(wave)}
        self.max_moves = MOVES_PER_ROUND
        self.votes: dict[str, list[dict]] = {}
        self.lock = threading.Lock()
        self.t0 = time.monotonic()

    # messages
    def msg(self, rnd: int, sender: str, body: dict, rationale: str, decision: dict | None = None) -> dict:
        m = {"round": rnd, "sender": sender, "type": body["type"], "body": body,
             "rationale": rationale}
        if decision is not None:
            m["decision"] = decision
        return m

    def emit(self, m: dict) -> None:
        m = {"seq": len(self.log), "round": m["round"], "ts": _now(), **m}  # ts stamped at emission
        self.log.append(m)
        if self.on_message is not None:
            self.on_message(m)

    def decider(self, agent: str, rnd: int, buf: list[dict]) -> Decider:
        """One Decider per agent so MODEL_FALLBACK names the agent; messages go to `buf`."""
        def hook(entry: str, nxt: str, err: str) -> None:
            buf.append(self.msg(rnd, agent, {"type": "MODEL_FALLBACK", "agent": agent, "from_model": entry,
                                             "to_model": nxt, "error": str(err)[:300]},
                                f"{entry} failed for {agent}; falling back to {nxt}"))
        return Decider(self.chain, on_fallback=hook)

    def account(self, r: DecisionResult, calls: int = 1) -> None:
        with self.lock:  # called from agent worker threads
            if r.provider != "formula":
                self.model_calls += calls
            self.fallbacks += len(r.fallback_from)
            self.model_errors.extend(r.errors)

    def reservation(self, agent: str, rnd: int) -> float:
        return round(self.best_seen[agent] * (0.9 - CONCESSION * (rnd - 1)), 3)

    def see(self, plan: JointPlan) -> None:
        """Anchor reservations on feasible plans only (an unsafe plan is not a real option)."""
        if not rules.feasible(self.wave, plan):
            return
        for ag in self.best_seen:
            self.best_seen[ag] = max(self.best_seen[ag], agent_utility(self.wave, plan, ag))


class S(TypedDict, total=False):
    ctx: Any
    round: int
    table: Any                  # JointPlan
    counters: list              # [(agent, JointPlan)]
    candidates: list            # distinct feasible JointPlans
    route: str
    final: Any                  # JointPlan
    outcome: str
    method: str


# ---------------------------------------------------------------- nodes

def _triage(st: S) -> dict:
    run: _Run = st["ctx"]
    w = run.wave
    worst_delay = max((max(float(f.delays.get("weather", 0) or 0), float(f.delays.get("late_aircraft", 0) or 0))
                       for f in w.flights), default=0.0)
    m = w.metar
    usable, total, max_gust = 0, 0, 0.0
    if m is not None:
        max_gust = float(max(m.wind_kt, m.gust_kt or 0))
        for r in w.airport.runways:
            if not r.open:
                continue
            total += 1
            hw, xw, tw = wind.components(m.wind_dir_deg, m.wind_kt, m.gust_kt, r.heading_true)
            lxw, ltw, _ = wind.limits(m.surface, None)
            usable += wind.within_limits(xw, tw, lxw, ltw)
    vis = m.visibility_sm if m is not None else None

    def formula() -> int:
        d = 0 if worst_delay < 15 else 1 if worst_delay < 30 else 2 if worst_delay < 60 else 3 if worst_delay < 120 else 4
        wx = 0
        if total and usable == 0:
            wx = 4
        elif total and usable < total / 2:
            wx = 3
        elif max_gust >= 30:
            wx = 2
        elif max_gust >= 20:
            wx = 1
        if vis is not None and vis < 3:
            wx = min(4, wx + 1)
        return int(max(d, wx))

    state = {"airport": w.airport.icao, "metar": m.raw if m else None, "runways_open": total,
             "runways_within_wind_limits": usable, "visibility_sm": vis, "worst_flight_delay_min": worst_delay,
             "flights": len(w.flights), "disrupted": sum(f.disrupted for f in w.flights),
             "queue": len(w.queue)}
    buf: list[dict] = []
    d = run.decider("ATC", 0, buf)  # triage has no agent; MODEL_FALLBACK is attributed to ATC
    r = d.score(state, "Rate the operational severity of this arrival-wave disruption at the hub.",
                SEVERITY_LEVELS, formula)
    run.account(r)
    for b in buf:
        run.emit(b)
    score = int(r.value)
    probs = [0.0] * len(SEVERITY_LEVELS)
    p = r.probabilities
    if isinstance(p, dict) and p:
        for i, lv in enumerate(SEVERITY_LEVELS):
            v = p.get(lv, p.get(str(i), p.get(i, 0.0)))
            probs[i] = float(v) if isinstance(v, (int, float)) else 0.0
    elif isinstance(p, (list, tuple)) and len(p) == len(SEVERITY_LEVELS):
        probs = [float(x) for x in p]
    if not any(probs):
        probs[score] = 1.0
    body = {"type": "TRIAGE", "score": score, "levels": SEVERITY_LEVELS, "probabilities": probs}
    if isinstance(r.confidence, (int, float)):
        body["confidence"] = float(r.confidence)
    if score >= SEVERE_LEVEL:
        run.max_moves = SEVERE_MOVES_PER_ROUND
    run.emit(run.msg(0, "DECIDER", body,
                     f"Severity {score}/4 (worst delay {worst_delay:g} min, {usable}/{total} runways within "
                     f"wind limits); {run.max_moves} moves per agent per round", _meta("score", r)))
    return {}


def _open(st: S) -> dict:
    run: _Run = st["ctx"]
    plan = moves.fcfs_plan(run.wave)
    plan.label = ""  # label = per-flight description; authorship lives in sender/rationale
    ev = evaluate(run.wave, plan)
    run.see(plan)
    run.known_rules |= {c["rule"] for c in ev["checks"] if not c["passed"]}
    nv = sum(not c["passed"] for c in ev["checks"])
    run.emit(run.msg(0, "ATC", {"type": "OPEN", "plan": plan.to_dict(), "evaluation": ev},
                     f"First come first served from the real schedule; {nv} rule violation(s) on the table"))
    return {"round": 1, "table": plan}


def _agent_turn(run: _Run, agent: str, rnd: int, table: JointPlan, known: set[str]) -> tuple[list[dict], JointPlan]:
    w = run.wave
    buf: list[dict] = []
    dec = run.decider(agent, rnd, buf)
    plan = table
    for _slot in range(run.max_moves):
        opts = moves.legal_moves(w, plan, agent)
        before_checks = rules.check(w, plan)
        before_v = _vkeys(before_checks)
        u_now = agent_utility(w, plan, agent)
        k_now = _known_violations(agent, before_checks, known)
        info = {}
        for i, mv in enumerate(opts):
            after = moves.apply(w, plan, mv)
            chk = rules.check(w, after)
            info[f"m{i + 1}"] = (mv, after, chk, agent_utility(w, after, agent), _known_violations(agent, chk, known))
        excluded: set[str] = set()
        moved = False
        stop = False
        for _attempt in range(MAX_REJECTIONS_PER_SLOT):  # each failed attempt is one rejection
            avail = {k: v for k, v in info.items() if k not in excluded}
            if not avail:
                stop = True
                break
            options = {k: f"{v[0].label} | your utility after {v[3]:.3f} (now {u_now:.3f})" for k, v in avail.items()}
            has_accept = any(v[0].kind == "accept" for v in avail.values())
            if not has_accept:
                options[PASS] = f"Make no further move | your utility stays {u_now:.3f}"

            def formula(avail=avail, has_accept=has_accept) -> str:
                best = min(avail, key=lambda k: (avail[k][4], -avail[k][3], k))
                b = avail[best]
                if b[0].kind != "accept" and (b[4] < k_now or (b[4] == k_now and b[3] > u_now + 1e-9)):
                    return best
                if has_accept:
                    return next(k for k, v in avail.items() if v[0].kind == "accept")
                return PASS

            state = {"role": _role(agent), "agent": agent, "round": rnd, "objective": OBJECTIVES[_role(agent)],
                     "view": _view(w, plan, agent),
                     "utility_terms": {k: round(v, 3) for k, v in agent_terms(w, plan, agent).items()},
                     "utility_now": u_now, "reservation": run.reservation(agent, rnd),
                     "rules_published_by_engine": sorted(known)}
            r = dec.choice(state, f"You are {agent}. Pick ONE move for your own authority that best serves your "
                                  "objective, or pass/accept. A safety engine will veto illegal moves.",
                           options, formula)
            run.account(r)
            pick = r.value
            if pick == PASS:
                stop = True
                break
            mv, after, chk, u_after, _ = avail[pick]
            if mv.kind == "accept" and plan is not table:
                stop = True  # pilot already moved this round: accepting just ends its turn, no extra message
                break
            new_v = [c for c in chk if not c.passed and (c.rule, c.callsign) not in before_v]
            if new_v:
                excluded.add(pick)
                known |= {c.rule for c in new_v}
                buf.append(run.msg(rnd, "ENGINE", {"type": "MOVE_REJECTED", "move": mv.to_dict(),
                                                   "violations": [c.to_dict() for c in new_v]},
                                   f"Vetoed {agent}'s move: {new_v[0].rule}: {new_v[0].detail}"[:300],
                                   _choice_meta(r, info)))
                continue
            buf.append(run.msg(rnd, agent, {"type": "MOVE", "move": mv.to_dict(), "plan": _plan_dict(w, after),
                                            "options_count": len(options)},
                               f"{mv.label}: own utility {u_now:.3f} -> {u_after:.3f}", _choice_meta(r, info)))
            plan = after
            moved = True
            if mv.kind == "accept":
                stop = True
            break
        if stop or not moved:
            break
    return buf, plan


def _choice_meta(r: DecisionResult, info: dict) -> dict:
    """DecisionMeta for a Choice, with probabilities keyed by Move.id (not the internal m1..m12 keys)."""
    m = _meta("choice", r)
    if "probabilities" in m:
        m["probabilities"] = {(info[k][0].id if k in info else k): v for k, v in m["probabilities"].items()}
    return m


def _moves(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd, table = st["round"], st["table"]
    agents = agent_ids(run.wave)
    known = set(run.known_rules)
    with ThreadPoolExecutor(max_workers=len(agents)) as ex:
        futs = [ex.submit(_agent_turn, run, ag, rnd, table, set(known)) for ag in agents]
        counters = []
        for ag, fut in zip(agents, futs):  # emit in protocol order as each agent finishes
            buf, plan = fut.result()
            for m in buf:
                if m["type"] == "MOVE_REJECTED":
                    run.known_rules |= {v["rule"] for v in m["body"]["violations"]}
                run.emit(m)
            counters.append((ag, plan))
    return {"counters": counters}


def _counter(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd = st["round"]
    out = []
    for ag, plan in st["counters"]:
        p = JointPlan(plan.assignments, "")
        ev = evaluate(run.wave, p)
        run.see(p)
        u = ev["utilities"][ag]
        same = p.id == st["table"].id
        run.emit(run.msg(rnd, ag, {"type": "COUNTER", "plan": p.to_dict(), "evaluation": ev},
                         ("Keeps the table plan" if same else "Counter-plan") + f"; own utility {u:.3f}"))
        out.append((ag, p))
    return {"counters": out}


def _check(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd = st["round"]
    seen, cands = set(), []
    for ag, p in st["counters"]:
        if p.id in seen:
            continue
        seen.add(p.id)
        v = rules.violations(run.wave, p)
        run.known_rules |= {c.rule for c in v}
        run.emit(run.msg(rnd, "ENGINE", {"type": "CHECK", "plan_id": p.id, "passed": not v,
                                         "violations": [c.to_dict() for c in v]},
                         f"{ag}'s plan passes all {len(rules.check(run.wave, p))} checks" if not v else
                         f"{ag}'s plan fails {len(v)} check(s): " + ", ".join(sorted({c.rule for c in v}))))
        if not v:
            cands.append(p)
    return {"candidates": cands}


def _vote(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd, cands = st["round"], st["candidates"]
    run.votes = {}
    if not cands:
        return {}
    w = run.wave
    agents = agent_ids(w)

    def one(ag: str):
        buf: list[dict] = []
        res = run.reservation(ag, rnd)
        us = {p.id: agent_utility(w, p, ag) for p in cands}
        items = {p.id: f"As {ag}, accept plan {p.id} ({p.describe()[:160]})? Your utility {us[p.id]:.3f}, "
                       f"your reservation {res:.3f}." for p in cands}
        state = {"role": _role(ag), "agent": ag, "round": rnd, "objective": OBJECTIVES[_role(ag)],
                 "reservation": res, "max_rounds": MAX_ROUNDS,
                 "plans": {p.id: {"utility": us[p.id], "terms": {k: round(v, 3) for k, v in
                                                                 agent_terms(w, p, ag).items()}} for p in cands}}
        rs = run.decider(ag, rnd, buf).noul_batch(state, items, lambda pid: 1.0 if us[pid] >= res else 0.0)
        return buf, res, us, rs

    with ThreadPoolExecutor(max_workers=len(agents)) as ex:
        futs = [ex.submit(one, ag) for ag in agents]
        for ag, fut in zip(agents, futs):
            buf, res, us, rs = fut.result()
            for m in buf:
                run.emit(m)
            first = next(iter(rs.values()))
            run.account(first)
            votes = []
            for p in cands:
                r = rs[p.id]
                acc = float(r.value) >= 0.5
                v = {"plan_id": p.id, "decision": "ACCEPT" if acc else "REJECT", "utility": us[p.id],
                     "reservation": res}
                if run.mode != "formulas":
                    v["p_accept"] = round(float(r.value), 3)
                if r.provider == "formula":
                    v["reason"] = f"utility {us[p.id]:.3f} {'>=' if acc else '<'} reservation {res:.3f}"
                else:
                    v["reason"] = f"{r.model}: P(accept)={float(r.value):.2f} (utility {us[p.id]:.3f}, reservation {res:.3f})"
                votes.append(v)
            run.votes[ag] = votes
            n_acc = sum(v["decision"] == "ACCEPT" for v in votes)
            run.emit(run.msg(rnd, ag, {"type": "VOTE", "votes": votes},
                             f"Accepts {n_acc}/{len(votes)} plan(s) at reservation {res:.3f}", _meta("noul", first)))
    return {}


def _decide_round(st: S) -> dict:
    run: _Run = st["ctx"]
    rnd, cands = st["round"], st["candidates"]
    w = run.wave
    agents = agent_ids(w)
    votes = run.votes if cands else {}
    unanimous = [p for p in cands if all(any(v["plan_id"] == p.id and v["decision"] == "ACCEPT"
                                             for v in votes.get(ag, [])) for ag in agents)]
    if unanimous:
        best = max(unanimous, key=lambda p: (*_maxmin(w, p), p.id))
        if rules.feasible(w, best):
            run.emit(run.msg(rnd, "ENGINE", {"type": "CONSENSUS", "plan_id": best.id, "accepted_by": agents},
                             f"All {len(agents)} agents accept {best.id}; max-min utility {_maxmin(w, best)[0]:.3f} "
                             f"among {len(unanimous)} unanimous plan(s)"))
            return {"route": "finalize", "final": best, "outcome": "consensus", "method": "consensus"}
    # after the last round there is no next round: report the reservations that applied in this one
    nxt = {ag: run.reservation(ag, rnd + 1 if rnd < MAX_ROUNDS else rnd) for ag in agents}
    run.emit(run.msg(rnd, "ENGINE", {"type": "NO_CONSENSUS", "valid_plan_ids": [p.id for p in cands],
                                     "next_reservation": nxt},
                     f"No plan accepted by all agents ({len(cands)} valid plan(s))"
                     + ("; agents concede" if rnd < MAX_ROUNDS else "; round limit reached")))
    return {"route": "concede" if rnd < MAX_ROUNDS else "fallback"}


def _concede(st: S) -> dict:
    run: _Run = st["ctx"]
    w = run.wave
    table = st["table"]
    plans = [p for _, p in st["counters"]]
    feas = [p for p in plans if rules.feasible(w, p)]
    if feas:
        table = max(feas, key=lambda p: (*_maxmin(w, p), p.id))
    elif plans:
        table = min(plans, key=lambda p: (len(rules.violations(w, p)), -_maxmin(w, p)[0], p.id))
    return {"round": st["round"] + 1, "table": table}


def _fallback(st: S) -> dict:
    run: _Run = st["ctx"]
    plan, method = fallback.solve(run.wave)
    reason = (f"No consensus after {MAX_ROUNDS} rounds" if st.get("outcome") != "unsafe"
              else "Agreed plan failed the final safety check")
    run.emit(run.msg(min(st.get("round", MAX_ROUNDS), MAX_ROUNDS), "ENGINE",
                     {"type": "FALLBACK", "plan_id": plan.id, "method": method, "reason": reason},
                     f"Deterministic solver (no AI): {method}"))
    return {"final": plan, "outcome": "fallback", "method": method}


def _finalize(st: S) -> dict:
    run: _Run = st["ctx"]
    plan = st["final"]
    if rules.feasible(run.wave, plan):
        return {}
    if st.get("outcome") == "consensus":  # agreed plan failed the final check: solver decides instead
        out = _fallback({**st, "outcome": "unsafe"})
        if rules.feasible(run.wave, out["final"]):
            return out
    raise RuntimeError("no feasible plan exists for this wave")


def build_graph():
    g = StateGraph(S)
    for name, fn in [("triage", _triage), ("open", _open), ("moves", _moves), ("counter", _counter),
                     ("check", _check), ("vote", _vote), ("decide_round", _decide_round),
                     ("concede", _concede), ("fallback", _fallback), ("finalize", _finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "triage")
    g.add_edge("triage", "open")
    g.add_edge("open", "moves")
    g.add_edge("moves", "counter")
    g.add_edge("counter", "check")
    g.add_edge("check", "vote")
    g.add_edge("vote", "decide_round")
    g.add_conditional_edges("decide_round", lambda st: st["route"],
                            {"finalize": "finalize", "concede": "concede", "fallback": "fallback"})
    g.add_edge("concede", "moves")
    g.add_edge("fallback", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def negotiate_wave(wave: Wave, mode: str, chain: list[str] | None = None,
                   on_message: Callable[[dict], None] | None = None, run_id: str | None = None) -> dict:
    global _GRAPH
    if mode not in ("formulas", "decision", "decision+claude"):
        raise ValueError(f"unknown mode {mode!r}")
    run = _Run(wave, mode, chain, on_message, run_id or uuid.uuid4().hex[:12])
    if _GRAPH is None:
        _GRAPH = build_graph()
    st = _GRAPH.invoke({"ctx": run}, config={"recursion_limit": 200})
    plan: JointPlan = st["final"]
    if not rules.feasible(wave, plan):
        raise RuntimeError("no feasible plan exists for this wave")
    plan.label = plan.describe()
    ev = evaluate(wave, plan)
    base = baseline.status_quo(wave)
    result = {
        "run_id": run.run_id, "wave_id": wave.id, "mode": mode,
        "model": run.chain[0] if run.chain else "formula",
        "outcome": st["outcome"], "rounds": min(st["round"], MAX_ROUNDS),
        "final_plan": plan.to_dict(), "evaluation": ev,
        "baseline": {"plan": None, "evaluation": evaluate(wave, base)},
        "narration": None,
        "model_errors": run.model_errors[:100],
        "stats": {"model_calls": run.model_calls, "fallbacks": run.fallbacks, "elapsed_ms": 0},
        "log": run.log,
    }
    result["baseline"]["plan"] = base.to_dict()
    if mode == "decision+claude":
        from atm.wave.narrate import brief
        nar = brief(result)
        err = nar.pop("error", None)  # keep the contract shape; surface the failure in model_errors
        if err:
            result["model_errors"].append(f"narration: {err}")
        result["narration"] = nar
    result["stats"]["elapsed_ms"] = int((time.monotonic() - run.t0) * 1000)
    return result


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()


if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--mermaid":  # python3 -m atm.wave.graph --mermaid docs/graph.mmd
        with open(sys.argv[2], "w") as fh:
            fh.write(mermaid())
        print(f"wrote {sys.argv[2]}")
        sys.exit(0)
    from tests.fixtures import make_wave
    r = negotiate_wave(make_wave(), "formulas", on_message=lambda m: print(m["seq"], m["round"], m["sender"], m["type"], m["rationale"]))
    print(json.dumps({k: r[k] for k in ("outcome", "rounds", "stats")}))
