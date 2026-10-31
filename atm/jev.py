"""Jev (TypeSafe AI) decision layer: fast typed decisions for each agent.

Jev makes the bounded choices (which plan to propose, accept or reject, how severe the
disruption is). It never decides legality: the constraint engine re-checks everything.
Each agent asks Jev separately and passes only its own private view as state.

API (docs.typesafe.ai/primitives):
    with TypeSafeClient() as client:
        r = client.system_one(state={...}, questions={"q": Choice(instructions=..., criteria={...})})
    r.answers["q"].choice / .probabilities / .confidence   (Choice)
    r.answers["q"].noul                                     (Noul: P(yes))
    r.answers["q"].score / .legend / .probabilities         (Score)
"""
from __future__ import annotations

import json
import os
import time

from . import plans as P

MODEL = os.environ.get("JEV_MODEL")  # pin a version in production; None = SDK default
SEVERITY_LEVELS = [
    "Minor: normal operations, small knock-on delay only.",
    "Moderate: reduced arrival rate, short holding, gates still flexible.",
    "Significant: one runway or key taxiway lost, holding and gate conflicts likely.",
    "Severe: heavy weather, long holding, fuel and crew limits in play, diversions possible.",
    "Critical: ground stop or airport closure conditions.",
]
ACCEPT_THRESHOLD = 0.5


def available() -> bool:
    if not os.environ.get("TYPESAFE_API_KEY"):
        return False
    try:
        _sdk()
        return True
    except ImportError:
        return False


def _sdk():
    import typesafe  # typesafe-sdk
    return typesafe


def _ask(state: dict, questions: dict):
    ts = _sdk()
    kwargs = {"state": state, "questions": questions}
    if MODEL:
        kwargs["model"] = MODEL
    t0 = time.time()
    with ts.TypeSafeClient() as client:
        resp = client.system_one(**kwargs)
    return resp, round(time.time() - t0, 3)


def _state(view: dict, extra: dict | None = None) -> dict:
    s = {"my_private_view": json.dumps(view, default=str)}
    if extra:
        s.update({k: json.dumps(v, default=str) if not isinstance(v, str) else v for k, v in extra.items()})
    return s


def _plan_desc(sc, p, agent) -> str:
    u, terms = P.utility(sc, p, agent)
    m = P.metrics(sc, p)
    keep = {k: m[k] for k in ("hold_min", "fuel_kg", "delay_min", "pax_protected", "pax_total",
                              "overtime_min", "next_dep_delay") if k in m}
    return f"{p.label()}. My decision-model score {u} (penalties {terms}). Effects: {keep}"


def choose(sc, agent: str, view: dict, objectives: str, pool: list, log_text: str) -> dict:
    """Jev Choice over the agent's shortlist. Returns ranked ids with probabilities."""
    ts = _sdk()
    criteria = {p.id: _plan_desc(sc, p, agent) for _, p in pool}
    q = ts.Choice(instructions=f"You are the {agent} agent. Objectives: {objectives} "
                               "Which plan should you propose so that all three agents can agree quickly "
                               "without sacrificing your objectives?", criteria=criteria)
    resp, dt = _ask(_state(view, {"negotiation_so_far": log_text}), {"proposal": q})
    a = resp.answers["proposal"]
    probs = dict(getattr(a, "probabilities", {}) or {})
    ranked = sorted(criteria, key=lambda i: (i != a.choice, -probs.get(i, 0)))
    return {"ranked": ranked, "choice": a.choice, "probabilities": probs,
            "confidence": getattr(a, "confidence", None), "latency_s": dt}


def accept(sc, agent: str, view: dict, objectives: str, plan_ids: list[str]) -> dict:
    """One Jev Noul per plan: P(this agent should accept). Batched into one request."""
    ts = _sdk()
    qs = {f"accept_{i}": ts.Noul(
        instructions=f"You are the {agent} agent. Objectives: {objectives} The safety engine has already "
                     f"verified this plan is legal. Should you accept it? Plan: "
                     f"{_plan_desc(sc, P.parse_plan(sc, pid), agent)}")
        for i, pid in enumerate(plan_ids)}
    if not qs:
        return {"p": {}, "latency_s": 0.0}
    resp, dt = _ask(_state(view), qs)
    return {"p": {pid: float(resp.answers[f"accept_{i}"].noul) for i, pid in enumerate(plan_ids)}, "latency_s": dt}


def severity(sc) -> dict:
    """Jev Score: how severe is this disruption (1-5)? Uses only the real flight record + weather."""
    ts = _sdk()
    f = sc.flight
    state = {"flight": json.dumps({k: f.get(k) for k in ("carrier", "flight_no", "origin", "dest", "weather_delay",
                                                         "late_aircraft_delay", "nas_delay", "arr_delay")}, default=str),
             "destination_weather": json.dumps(sc.weather), "runways": json.dumps(sc.runways),
             "blocked_taxiway": json.dumps(sc.blocked_taxiway)}
    resp, dt = _ask(state, {"severity": ts.Score(instructions="How severe is this arrival disruption for the "
                                                              "destination hub?", criteria=SEVERITY_LEVELS)})
    a = resp.answers["severity"]
    return {"score": a.score, "legend": getattr(a, "legend", None),
            "probabilities": getattr(a, "probabilities", None), "confidence": getattr(a, "confidence", None),
            "latency_s": dt}
