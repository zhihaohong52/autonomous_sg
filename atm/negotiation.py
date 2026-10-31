"""Three-agent negotiation: ATC, PILOT and GROUND each hold a private view and decision model.

Protocol (max 3 rounds):
  1. PROPOSE  - every agent posts a shortlist of plans, chosen from its OWN view. Agents can't
                see other domains, so round-1 proposals often break someone else's rules.
  2. CHECK    - the deterministic constraint engine checks every proposed plan against ALL
                rules. Violations are published; the violated rule becomes known to everyone
                and later rounds pre-check it.
  3. VOTE     - every agent ACCEPTs or REJECTs each valid plan proposed by the others.
  4. CONSENSUS if some plan is valid and accepted by all three (pick the max-min utility one).
     Otherwise each agent concedes (lowers its reservation utility) and the next round starts.
  After round 3 without consensus, a deterministic solver (max-min utility over all feasible
  plans, no AI) decides and the log says so.

Policies: `rule` (deterministic decision model, no LLM) or `llm` (one separate Claude call per
agent per phase, choosing from its decision model's shortlist). The LLM never decides
legality: every plan it touches is re-checked by the engine.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from . import jev
from . import plans as P
from .scenario import Scenario

MAX_ROUNDS = 3
SHORTLIST = {1: 3, 2: 5, 3: 8}
CONCESSION = 0.12
OBJECTIVES = {
    "ATC": "Keep runway wake separation, avoid blocked taxiways, maximise sector/runway throughput, minimise airborne holding.",
    "PILOT": "Preserve fuel reserves, keep the flight safe, minimise time holding in turbulence, protect passengers' connections.",
    "GROUND": "Optimise gate utilisation, avoid ground-crew overtime, minimise turnaround delay for the aircraft's next departure.",
}


@dataclass
class Msg:
    round: int
    sender: str
    type: str  # PROPOSE | CHECK | VOTE | CONSENSUS | NO_CONSENSUS | FALLBACK
    body: dict
    rationale: str = ""
    ts: float = field(default_factory=time.time)


# ------------------------------------------------------------------ private views

def private_view(sc: Scenario, agent: str) -> dict:
    """What each agent can see. Nobody sees the other domains' data."""
    f = sc.flight
    common = {"flight": sc.callsign, "route": f"{f['origin']}->{f['dest']}", "aircraft_wake": sc.wake,
              "disruption": sc.disruption, "eta_earliest_min": sc.eta_earliest}
    if agent == "ATC":
        return {**common, "runways": sc.runways, "blocked_taxiway": sc.blocked_taxiway,
                "weather_separation_factor": sc.weather["separation_factor"],
                "traffic": [asdict(a) for a in sc.arrivals if -10 <= a.time <= 70]}
    if agent == "PILOT":
        return {**common, "fuel": sc.fuel, "weather": sc.weather,
                "connections": [asdict(c) for c in sc.connections]}
    return {**common, "scheduled_gate": sc.scheduled_gate, "aircraft_gate_size": sc.gate_size,
            "gates": [asdict(g) for g in sc.gates], "crew_shift_end_min": sc.crew_shift_end,
            "crew_max_overtime_min": sc.crew_max_overtime, "next_departure_min": sc.next_departure}


# ------------------------------------------------------------------ candidate pools

def _pool(sc, cands, agent, known_rules: set[str], rejected: set[str], k: int):
    """Agent's best plans by its own decision model: own rules + rules learned from rejections."""
    out, seen = [], set()
    scored = sorted(((P.utility(sc, p, agent)[0], p) for p in cands if p.id not in rejected),
                    key=lambda x: -x[0])
    for u, p in scored:
        key = (p.runway, p.gate)
        if key in seen:
            continue  # one time per runway/gate pair keeps the shortlist diverse
        checks = P.constraints(sc, p)
        if not all(c.passed for c in checks if c.owner == agent or c.rule in known_rules):
            continue
        seen.add(key)
        out.append((u, p))
        if len(out) >= k:
            break
    return out


# ------------------------------------------------------------------ LLM policy

class ProposalOut(BaseModel):
    plan_ids: list[str] = Field(description="Plan ids to propose, best first, chosen from the candidate list")
    rationale: str = Field(description="One or two sentences, from your role's point of view")


class VoteItem(BaseModel):
    plan_id: str
    decision: Literal["ACCEPT", "REJECT"]
    reason: str


class VoteOut(BaseModel):
    votes: list[VoteItem]


def _llm_client():
    import anthropic
    return anthropic.Anthropic()


MODEL = os.environ.get("ATM_MODEL", "claude-opus-5-5")


def _llm(system: str, user: str, schema):
    client = _llm_client()
    resp = client.messages.parse(
        model=MODEL, max_tokens=4000, system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema, output_config={"effort": "low"},
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"LLM returned no structured output ({resp.stop_reason})")
    return resp.parsed_output


def _system(agent: str) -> str:
    return (f"You are the {agent} agent in an air-traffic disruption negotiation with two other "
            f"independent agents. Your objectives: {OBJECTIVES[agent]} "
            "You only know your own domain. Safety rules are enforced by a separate deterministic "
            "constraint engine; you cannot override it. Choose only from the candidate plans you are "
            "given (each scored by your decision model, higher is better for you). Aim for an agreement "
            "within 3 rounds: concede when your score stays above your reservation value.")


def _fmt_log(log: list[Msg]) -> str:
    lines = []
    for m in log:
        if m.type == "PROPOSE":
            lines.append(f"R{m.round} {m.sender} proposed {m.body['plan_ids']}")
        elif m.type == "CHECK" and not m.body["passed"]:
            lines.append(f"R{m.round} ENGINE rejected {m.body['plan_id']}: {', '.join(v['rule'] for v in m.body['violations'])}")
        elif m.type == "VOTE":
            lines.append(f"R{m.round} {m.sender} " + "; ".join(f"{v['plan_id']}={v['decision']}" for v in m.body["votes"]))
    return "\n".join(lines) or "(none yet)"


# ------------------------------------------------------------------ negotiation

def negotiate(sc: Scenario, policy: str = "rule", engine: bool = True) -> dict:
    """engine=False is an ablation for evaluation only: proposals skip the cross-domain check."""
    t0 = time.time()
    cands = P.generate(sc)
    log: list[Msg] = []
    known_rules: set[str] = set()
    rejected: set[str] = set()
    best_own = {a: (_pool(sc, cands, a, set(), set(), 1) or [(0.5, None)])[0][0] for a in P.AGENTS}
    llm_errors = []
    use_jev = policy.startswith("jev")
    bonus = 0
    if use_jev:
        try:
            sev = jev.severity(sc)
            log.append(Msg(0, "JEV", "TRIAGE", sev, f"Jev Score: disruption severity {sev['score']}"))
            bonus = 2 if _num(sev["score"]) >= 4 else 0
        except Exception as e:
            llm_errors.append(f"Jev severity: {e}")

    def reservation(a, r):
        return round(best_own[a] * (0.9 - CONCESSION * (r - 1)), 3)

    for r in range(1, MAX_ROUNDS + 1):
        # ---- PROPOSE (agents act independently, in parallel)
        def propose(a):
            k = 12 if policy == "llm" else 8 if use_jev else SHORTLIST[r]
            pool = _pool(sc, cands, a, known_rules, rejected, k + bonus)
            pool = [(u, p) for u, p in pool if u >= reservation(a, r)] or pool[:1]
            ids = [p.id for _, p in pool[:SHORTLIST[r] + bonus]]
            why = f"Top plans by my decision model (reservation {reservation(a, r)})."
            extra = {}
            if use_jev and pool:
                try:
                    out = jev.choose(sc, a, private_view(sc, a), OBJECTIVES[a], pool, _fmt_log(log))
                    ids = out["ranked"][:SHORTLIST[r] + bonus]
                    extra = {"jev": out}
                    conf = out["confidence"]
                    why = (f"Jev Choice picked {out['choice']} (p={out['probabilities'].get(out['choice'], 0):.2f}"
                           + (f", confidence {conf:.2f}" if isinstance(conf, (int, float)) else "") + ").")
                except Exception as e:
                    llm_errors.append(f"R{r} {a} Jev choose: {e}")
            if policy == "llm":
                try:
                    out = _llm(_system(a), _proposal_prompt(sc, a, r, pool, log, reservation(a, r)), ProposalOut)
                    valid = {p.id for _, p in pool}
                    chosen = [i for i in out.plan_ids if i in valid][:SHORTLIST[r]]
                    if chosen:
                        ids, why = chosen, out.rationale
                    else:
                        llm_errors.append(f"R{r} {a}: proposed no valid ids {out.plan_ids}")
                except Exception as e:  # deterministic policy stands in; logged, never silent
                    llm_errors.append(f"R{r} {a} propose: {e}")
            return a, ids, why, extra

        with ThreadPoolExecutor(3) as ex:
            props = list(ex.map(propose, P.AGENTS))
        on_table: dict[str, str] = {}
        for a, ids, why, extra in props:
            log.append(Msg(r, a, "PROPOSE", {"plan_ids": ids, **extra,
                                             "plans": [P.plan_card(sc, P.parse_plan(sc, i), False) for i in ids]}, why))
            for i in ids:
                on_table.setdefault(i, a)

        # ---- CHECK (deterministic engine, all rules)
        valid = []
        for pid, owner in on_table.items():
            checks = P.constraints(sc, P.parse_plan(sc, pid))
            bad = [asdict(c) for c in checks if not c.passed]
            log.append(Msg(r, "ENGINE", "CHECK", {"plan_id": pid, "proposed_by": owner, "passed": not bad,
                                                  "violations": bad, "checks": [asdict(c) for c in checks]}))
            if bad and engine:
                rejected.add(pid)
                known_rules.update(v["rule"] for v in bad)
            else:
                valid.append(pid)

        # ---- VOTE
        def vote(a):
            items = []
            for pid in valid:
                u, terms = P.utility(sc, P.parse_plan(sc, pid), a)
                own = on_table[pid] == a
                ok = own or u >= reservation(a, r)
                items.append({"plan_id": pid, "decision": "ACCEPT" if ok else "REJECT", "utility": u,
                              "reason": "my proposal" if own else
                              (f"utility {u} >= reservation {reservation(a, r)}" if ok else
                               f"utility {u} < reservation {reservation(a, r)}; worst term {min(terms, key=terms.get)}")})
            why = "Decision-model vote."
            others = [i for i in items if on_table[i["plan_id"]] != a]
            if use_jev and others:
                try:
                    out = jev.accept(sc, a, private_view(sc, a), OBJECTIVES[a], [i["plan_id"] for i in others])
                    for it in others:
                        pa = out["p"][it["plan_id"]]
                        it["p_accept"] = round(pa, 3)
                        it["decision"] = "ACCEPT" if pa >= jev.ACCEPT_THRESHOLD else "REJECT"
                        it["reason"] = f"Jev P(accept)={pa:.2f}; decision-model score {it['utility']}"
                    why = f"Jev Noul vote, accept if P >= {jev.ACCEPT_THRESHOLD} ({out['latency_s']}s)."
                except Exception as e:
                    llm_errors.append(f"R{r} {a} Jev vote: {e}")
            elif policy == "llm" and others:
                try:
                    out = _llm(_system(a), _vote_prompt(sc, a, r, others, log, reservation(a, r)), VoteOut)
                    by_id = {v.plan_id: v for v in out.votes}
                    for it in others:
                        if it["plan_id"] in by_id:
                            v = by_id[it["plan_id"]]
                            it["decision"], it["reason"] = v.decision, v.reason
                    why = "LLM vote (engine-validated plans only)."
                except Exception as e:
                    llm_errors.append(f"R{r} {a} vote: {e}")
            return a, items, why

        with ThreadPoolExecutor(3) as ex:
            votes = list(ex.map(vote, P.AGENTS))
        accepted_by = {pid: set() for pid in valid}
        for a, items, why in votes:
            log.append(Msg(r, a, "VOTE", {"votes": items}, why))
            for it in items:
                if it["decision"] == "ACCEPT":
                    accepted_by[it["plan_id"]].add(a)

        agreed = [pid for pid, s in accepted_by.items() if s == set(P.AGENTS)]
        if agreed:
            pid = max(agreed, key=lambda i: _fairness(sc, i))
            log.append(Msg(r, "ENGINE", "CONSENSUS", {"plan_id": pid, "candidates": agreed},
                           "All three agents accepted; picked the plan whose worst-off agent is best off."))
            return _result(sc, policy, log, pid, "consensus", r, t0, llm_errors)
        for pid in valid:  # a valid plan someone rejected stays off later shortlists
            rejected.add(pid)
        log.append(Msg(r, "ENGINE", "NO_CONSENSUS", {"valid": valid, "known_rules": sorted(known_rules)},
                       "No plan accepted by all; agents concede and re-propose." if r < MAX_ROUNDS else ""))

    pid, why = fallback(sc, cands)
    log.append(Msg(MAX_ROUNDS, "ENGINE", "FALLBACK", {"plan_id": pid}, why))
    return _result(sc, policy, log, pid, "fallback", MAX_ROUNDS, t0, llm_errors)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _fairness(sc, pid):
    us = [P.utility(sc, P.parse_plan(sc, pid), a)[0] for a in P.AGENTS]
    return (min(us), sum(us))


def fallback(sc: Scenario, cands=None) -> tuple[str, str]:
    """Deterministic, no-AI resolution: max-min utility over every fully feasible plan."""
    cands = cands or P.generate(sc)
    ok = [p for p in cands if p.kind == "land" and P.feasible(sc, p)]
    if not ok:
        return "DIVERT", "No feasible landing plan within 60 min: divert to alternate."
    best = max(ok, key=lambda p: _fairness(sc, p.id))
    return best.id, f"Deterministic solver: max-min utility over {len(ok)} feasible plans."


class Narration(BaseModel):
    lines: list[str] = Field(description="One short plain-English line per agent message, in log order, "
                                         "prefixed with the round and agent, e.g. 'R1 PILOT: ...'")
    brief: str = Field(description="Three-sentence brief for the airline duty manager: what happened, the agreed "
                                   "plan and its trade-offs, and anything they should double-check")


def narrate(sc: Scenario, log: list[dict], plan_label: str, outcome: str) -> dict:
    """Claude writes the human-readable story of the negotiation. It decides nothing; on any
    failure a template brief is used instead."""
    tmpl = (f"{sc.callsign} {sc.flight['origin']}->{sc.flight['dest']}: {', '.join(sc.disruption)}. "
            f"Outcome: {outcome}, plan '{plan_label}'. All 8 safety rules pass for this plan.")
    try:
        compact = [{k: m[k] for k in ("round", "sender", "type", "rationale")} |
                   {"summary": _fmt_log([Msg(**{**m, "ts": 0})])} for m in log if m["type"] != "CHECK" or not m["body"]["passed"]]
        out = _llm("You write concise operational summaries for an airline hub duty manager. Use only facts "
                   "present in the log; do not add numbers that are not in it.",
                   f"Negotiation log: {compact}\nFinal plan: {plan_label} ({outcome}).", Narration)
        return {"source": "claude", "model": MODEL, "lines": out.lines, "brief": out.brief}
    except Exception as e:
        return {"source": "template", "error": str(e)[:200], "lines": [], "brief": tmpl}


def _result(sc, policy, log, pid, outcome, rounds, t0, llm_errors):
    plan = P.parse_plan(sc, pid)
    logd = [asdict(m) for m in log]
    narration = narrate(sc, logd, plan.label(), outcome) if policy == "jev+claude" else None
    return {"narration": narration,"flight_id": sc.flight["id"], "policy": policy, "outcome": outcome, "rounds": rounds,
            "plan": P.plan_card(sc, plan), "log": logd,
            "violations_in_final": [asdict(c) for c in P.constraints(sc, plan) if not c.passed],
            "elapsed_s": round(time.time() - t0, 2), "llm_errors": llm_errors}


def _proposal_prompt(sc, a, r, pool, log, res):
    import json
    cands = [{"plan_id": p.id, "label": p.label(), "your_score": u,
              "score_terms": P.utility(sc, p, a)[1], "metrics": P.metrics(sc, p)} for u, p in pool]
    return (f"Round {r} of {MAX_ROUNDS}. Your reservation score this round: {res}.\n"
            f"Your private view:\n{json.dumps(private_view(sc, a), default=str)}\n\n"
            f"Negotiation so far:\n{_fmt_log(log)}\n\n"
            f"Candidate plans (already pass your own rules and every rule the engine has flagged so far):\n"
            f"{json.dumps(cands)}\n\nPropose up to {SHORTLIST[r]} plan ids, best first. Prefer plans the "
            f"others are likely to accept given what they rejected before.")


def _vote_prompt(sc, a, r, items, log, res):
    import json
    view = [{**i, "label": P.parse_plan(sc, i["plan_id"]).label(),
             "score_terms": P.utility(sc, P.parse_plan(sc, i["plan_id"]), a)[1]} for i in items]
    return (f"Round {r} of {MAX_ROUNDS}. Your reservation score: {res}. The engine has verified these plans "
            f"are safe and legal. Your private view:\n{json.dumps(private_view(sc, a), default=str)}\n\n"
            f"Negotiation so far:\n{_fmt_log(log)}\n\nPlans proposed by the other agents, with your decision-model "
            f"score:\n{json.dumps(view)}\n\nVote ACCEPT or REJECT on every plan, with a one-sentence reason.")
