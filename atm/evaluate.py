"""Batch evaluation over real disrupted flights.

    python -m atm.evaluate --n 500            # decision-model agents (no API key needed)
    python -m atm.evaluate --n 500 --compare jev --compare-n 50   # plus 50 flights with Jev decisions

Compares, per flight:
  * negotiated plan (3 agents + engine)
  * status-quo baseline (FCFS slot, scheduled gate, no coordination)
  * centralised optimum (one solver that sees everything: max-min utility) - an upper bound
    on what negotiation could reach, so we can report the cost of decentralisation
  * ablation: the same negotiation with the safety engine switched off
Writes eval/results.json and eval/REPORT.md.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import time
from collections import Counter
from pathlib import Path

from . import baseline, negotiation
from . import plans as P
from .scenario import build, disruptions

OUT = Path(__file__).resolve().parent.parent / "eval"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 2) if xs else None


def run_one(fid: int, policy: str) -> dict:
    sc = build(fid)
    res = negotiation.negotiate(sc, policy)
    base = baseline.run(sc)
    opt_id, _ = negotiation.fallback(sc)
    opt = P.plan_card(sc, P.parse_plan(sc, opt_id))
    abl = negotiation.negotiate(sc, "rule", engine=False)
    m = res["plan"]["metrics"]
    r1_viol = [v["rule"] for msg in res["log"] if msg["type"] == "CHECK" and msg["round"] == 1
               for v in msg["body"]["violations"]]
    return {
        "flight_id": fid, "callsign": sc.callsign, "route": f"{sc.flight['origin']}->{sc.flight['dest']}",
        "outcome": res["outcome"], "rounds": res["rounds"], "plan": res["plan"]["label"],
        "diverted": res["plan"]["plan"]["kind"] == "divert",
        "final_violations": len(res["violations_in_final"]),
        "engine_rejections": sum(1 for x in res["log"] if x["type"] == "CHECK" and not x["body"]["passed"]),
        "r1_violation_rules": r1_viol,
        "min_utility": min(res["plan"]["utility"].values()),
        "opt_min_utility": min(opt["utility"].values()),
        "ablation_unsafe": len(abl["violations_in_final"]) > 0,
        "ablation_rules": [v["rule"] for v in abl["violations_in_final"]],
        "neg": m, "base": base, "elapsed_s": res["elapsed_s"], "llm_errors": res["llm_errors"],
    }


def summarise(rows: list[dict]) -> tuple[dict, dict]:
    n = len(rows)
    pax = lambda side: round(100 * sum(r[side]["pax_protected"] for r in rows) / max(1, sum(r[side]["pax_total"] for r in rows)), 1)
    summary = {
        "consensus_rate_pct": round(100 * sum(r["outcome"] == "consensus" for r in rows) / n, 1),
        "fallback_rate_pct": round(100 * sum(r["outcome"] == "fallback" for r in rows) / n, 1),
        "mean_rounds": _mean([r["rounds"] for r in rows]),
        "agreed_in_round": dict(sorted(Counter(r["rounds"] for r in rows if r["outcome"] == "consensus").items())),
        "final_plans_violating_a_rule": sum(r["final_violations"] > 0 for r in rows),
        "engine_rejections_per_flight": _mean([r["engine_rejections"] for r in rows]),
        "round1_violations_by_rule": dict(Counter(v for r in rows for v in r["r1_violation_rules"]).most_common()),
        "ablation_no_engine_unsafe_agreements_pct": round(100 * sum(r["ablation_unsafe"] for r in rows) / n, 1),
        "ablation_rules": dict(Counter(v for r in rows for v in r["ablation_rules"]).most_common()),
        "fairness_vs_central_optimum": round(_mean([r["min_utility"] for r in rows]) / max(1e-9, _mean([r["opt_min_utility"] for r in rows])), 3),
        "mean_latency_s": _mean([r["elapsed_s"] for r in rows]),
    }
    side = lambda s: {
        "delay_min": _mean([r[s]["delay_min"] for r in rows]),
        "fuel_kg": _mean([r[s]["fuel_kg"] for r in rows]),
        "hold_min": _mean([r[s]["hold_min"] for r in rows]),
        "connections_protected_pct": pax(s),
        "crew_overtime_min": _mean([r[s]["overtime_min"] for r in rows]),
        "next_departure_delay_min": _mean([r[s]["next_dep_delay"] for r in rows]),
        "diversion_pct": round(100 * sum(bool(r[s].get("diverted", r.get("diverted"))) if s == "base" else r["diverted"] for r in rows) / n, 1),
    }
    neg, base = side("neg"), side("base")
    neg["crew_duty_breaches"] = sum(r["final_violations"] > 0 for r in rows)
    base["crew_duty_breaches"] = sum(bool(r["base"].get("crew_duty_breach")) for r in rows)
    summary.update(neg)
    return summary, base


def failures(rows: list[dict], k: int = 12) -> list[dict]:
    out = []
    for r in rows:
        reasons = []
        if r["outcome"] == "fallback":
            reasons.append("no consensus in 3 rounds -> deterministic fallback")
        if r["diverted"]:
            reasons.append("diverted: no feasible landing plan within 60 min")
        if r["neg"]["delay_min"] > r["base"]["delay_min"] + 1:
            reasons.append(f"slower than baseline ({r['neg']['delay_min']} vs {r['base']['delay_min']} min): "
                           "agents traded delay for fuel/crew/connections")
        if r["neg"]["fuel_kg"] > r["base"]["fuel_kg"] + 50:
            reasons.append(f"burns more fuel than baseline ({r['neg']['fuel_kg']} vs {r['base']['fuel_kg']} kg)")
        if r["llm_errors"]:
            reasons.append(f"LLM error, decision model stood in: {r['llm_errors'][0][:120]}")
        if reasons:
            out.append({"flight_id": r["flight_id"], "callsign": r["callsign"], "route": r["route"],
                        "plan": r["plan"], "reason": "; ".join(reasons)})
    return out[:k]


def report(res: dict) -> str:
    s, b = res["summary"], res["baseline"]
    lines = [f"# Evaluation: {res['n']} real disrupted flights ({res['policy']} agents)", "",
             f"Data: BTS on-time records 2023-2025 filtered to WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20 "
             f"({res['population']} flights). Airport state around each flight is simulated (seeded per flight).", "",
             "| Metric | Negotiated | Status quo |", "|---|---|---|"]
    for k in b:
        lines.append(f"| {k} | {s.get(k)} | {b[k]} |")
    lines += ["", "| Process metric | Value |", "|---|---|"]
    for k, v in s.items():
        if k not in b:
            lines.append(f"| {k} | {v} |")
    lines += ["", "## Failure cases", ""] + [f"- **{f['callsign']} {f['route']}** (id {f['flight_id']}): {f['reason']}"
                                             for f in res["failures"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--compare", default="", help="extra policy to evaluate: jev | jev+claude | llm")
    ap.add_argument("--compare-n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    ids = disruptions().id.tolist()
    sample = random.Random(a.seed).sample(ids, min(a.n, len(ids)))
    OUT.mkdir(exist_ok=True)
    t = time.time()
    rows = [run_one(i, "rule") for i in sample]
    summary, base = summarise(rows)
    res = {"n": len(rows), "policy": "rule", "population": len(ids), "seed": a.seed, "summary": summary,
           "baseline": base, "failures": failures(rows), "runtime_s": round(time.time() - t, 1)}
    if a.compare:
        sub = sample[: a.compare_n]
        crows = [run_one(i, a.compare) for i in sub]
        cs, cb = summarise(crows)
        rule_by_id = {r["flight_id"]: r for r in rows}
        same = sum(r["plan"] == rule_by_id[r["flight_id"]]["plan"] for r in crows)
        rs, _ = summarise([rule_by_id[i] for i in sub])
        res["compare"] = {"policy": a.compare, "n": len(crows), "summary": cs, "baseline": cb,
                          "rule_on_same_flights": rs,
                          "same_final_plan_as_rule_pct": round(100 * same / len(crows), 1),
                          "flights_with_model_errors": sum(bool(r["llm_errors"]) for r in crows),
                          "failures": failures(crows)}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    (OUT / "rows.json").write_text(json.dumps(rows, default=str))
    (OUT / "REPORT.md").write_text(report(res))
    print(report(res))


if __name__ == "__main__":
    main()
