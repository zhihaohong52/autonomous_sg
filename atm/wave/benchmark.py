"""Benchmark: decision-model arms vs the formula arm on the same waves (spec §10, §14; contract BenchmarkReport).

    python3 -m atm.wave.benchmark --source flights2015 --n 50 --arms openrouter:typesafe/jev-1.14,formula --concurrency 16 --seed 7

Definitions (also written to selection.reasons):
- fallback_rate_pct = % of waves whose outcome was the deterministic solver fallback.
- error_rate_pct    = % of decisions (all start at the arm) answered after the arm failed (non-empty fallback_from).
- decisions         = DecisionMeta of AGENT_TURN votes and of every move/rejected move, plus TRIAGE.
- latency_*         = DecisionMeta.latency_ms of model-answered decisions only (null when there are none).
- failed waves      = non-consensus + one error decision; outcome means use the waves that succeeded in every arm.
- brier             = mean (p_accept - y)^2 over model-answered votes (AGENT_TURN body.votes[]), y = formula verdict (utility >= reservation).
- status quo may violate rules: baseline means cover all common waves, feasible_only covers the waves where it is
  feasible, and regressions vs status quo are only reported on those waves.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from atm.wave import rules
from atm.wave.graph import negotiate_wave
from atm.wave.model import Assignment, JointPlan
from atm.wave.utility import evaluate

ROOT = Path(__file__).resolve().parents[2]
DEAD_MODEL = "openrouter:invalid/model-does-not-exist"
P95_LIMIT_MS, ERR_LIMIT_PCT, CONSENSUS_LIMIT_PCT = 1000, 2.0, 90.0
TOTAL_KEYS = ("delay_min", "fuel_kg", "pax_protected_pct", "overtime_min")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- waves

def fixture_waves(n: int) -> list:
    from dataclasses import replace
    from atm.wave.fixtures import make_wave
    out = []
    for i in range(n):
        w = make_wave()
        flights = [replace(f, delays={**f.delays, "weather": 10 + 7 * ((i + j) % 4)},
                           disrupted=((i + j) % 3 == 0),
                           fuel={**f.fuel, "extra_min": f.fuel["extra_min"] - (i % 3) * 4})
                   for j, f in enumerate(w.flights)]
        out.append(replace(w, id=f"{w.id}-fx{i}", flights=flights))
    return out


def sample_wave_ids(n: int, seed: int) -> list[str]:
    from atm.wave.build import list_waves
    rows = list_waves(limit=10000)
    by_ap: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_ap[r["airport"]["iata"]].append(r["id"])
    rng = random.Random(seed)
    for ids in by_ap.values():
        rng.shuffle(ids)
    airports = sorted(by_ap)
    rng.shuffle(airports)
    out: list[str] = []
    while len(out) < n and any(by_ap.values()):  # round-robin = stratified by airport
        for ap in airports:
            if by_ap[ap] and len(out) < n:
                out.append(by_ap[ap].pop())
    return out


def sample_flights2015_ids(n: int, seed: int) -> list[str]:
    """Seeded sample of flights.csv rows, round-robin (stratified) over the 30 hub destinations so IEM weather
    is fetched for a limited set of stations. Airports without runway data are skipped by flights2015."""
    from atm.wave import flights2015
    from atm.wave.hubs import HUBS
    rng = random.Random(seed)
    by_ap: dict[str, list[str]] = {}
    for ap in HUBS:
        rows = flights2015.list_rows(airport=ap, limit=10 ** 7)
        if rows:
            ids = [r["wave_id"] for r in rows]
            rng.shuffle(ids)
            by_ap[ap] = ids
    airports = sorted(by_ap)
    rng.shuffle(airports)
    out: list[str] = []
    while len(out) < n and any(by_ap.values()):
        for ap in airports:
            if by_ap[ap] and len(out) < n:
                out.append(by_ap[ap].pop())
    return out


def load_waves(n: int, seed: int, fixtures: bool, source: str = "flights2015") -> tuple[list, list[dict]]:
    """Returns (waves, build-failure rows)."""
    if fixtures:
        return fixture_waves(n), []
    if source == "flights2015":
        from atm.wave import flights2015
        ids, build = sample_flights2015_ids(n, seed), lambda wid: flights2015.build_from_row(int(wid[len("F2015-"):]))
    else:
        from atm.wave.build import build_wave
        ids, build = sample_wave_ids(n, seed), build_wave
    waves, bad = [], []
    for wid in ids:
        try:
            waves.append(build(wid))
        except Exception as e:  # noqa: BLE001
            log(f"skip {wid}: build failed: {e}")
            bad.append({"wave_id": wid, "model": "-", "reason": f"build failed: {str(e)[:200]}"})
    return waves, bad


# ---------------------------------------------------------------- running

def decisions(result: dict) -> list[dict]:
    """Every DecisionMeta in a result log: message-level decisions (TRIAGE, AGENT_TURN vote; the body.decision of an
    AGENT_TURN is the same object) plus the per-move decisions in AGENT_TURN moves[] and rejected[]."""
    out = []
    for m in result["log"]:
        body = m.get("body") or {}
        d = m.get("decision") or body.get("decision")
        if d:
            out.append(d)
        if m["type"] == "AGENT_TURN":
            out += [x["decision"] for x in body.get("moves", []) + body.get("rejected", []) if x.get("decision")]
    return out


def arm_parts(arm: str) -> tuple[str, str]:
    if arm == "formula":
        return "formula", "formula"
    prov, _, model = arm.partition(":")
    return prov, model


def run_arm(arm: str, waves: list, chain: list[str], mode: str, concurrency: int, tag: str) -> dict[str, dict]:
    from atm.decide import _reset_breakers
    _reset_breakers()
    out: dict[str, dict] = {}

    def one(w):
        t0 = time.monotonic()
        try:
            r = negotiate_wave(w, mode, chain=chain)
            out[w.id] = {"result": r}
            log(f"[{tag}] {w.id}: {r['outcome']} rounds={r['rounds']} delay={r['evaluation']['totals']['delay_min']} "
                f"({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            out[w.id] = {"error": f"{type(e).__name__}: {e}"[:300]}
            log(f"[{tag}] {w.id}: EXCEPTION {e}")

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        list(ex.map(one, waves))
    return out


@contextmanager
def engine_disabled():
    """Not thread-safe: replaces the rule engine's verdicts for the duration of the block."""
    saved = (rules.feasible, rules.violations, rules.check)
    rules.feasible = lambda wave, plan: True
    rules.violations = lambda wave, plan: []
    rules.check = lambda wave, plan, mm=None: []
    try:
        yield
    finally:
        rules.feasible, rules.violations, rules.check = saved


def plan_from_dict(d: dict) -> JointPlan:
    fields = ("callsign", "action", "runway", "slot_min", "gate", "speed_reduction_min", "ground_delay_min")
    return JointPlan([Assignment(**{k: a[k] for k in fields if k in a}) for a in d["assignments"]], d.get("label", ""))


def mean(xs: list[float]) -> float:
    return round(statistics.fmean(xs), 3) if xs else 0.0


def ci95(xs: list[float]) -> list[float] | None:
    """mean +/- 1.96*sd/sqrt(n); null when n < 2."""
    if len(xs) < 2:
        return None
    m, h = statistics.fmean(xs), 1.96 * statistics.stdev(xs) / len(xs) ** 0.5
    return [round(m - h, 3), round(m + h, 3)]


def pct(num: float, den: float) -> float:
    return round(100.0 * num / den, 2) if den else 0.0


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def brier(results: list[dict]) -> float:
    errs = []
    for r in results:
        for m in r["log"]:
            body = m.get("body") or {}
            if m["type"] not in ("VOTE", "AGENT_TURN") or \
                    (m.get("decision") or body.get("decision") or {}).get("provider", "formula") == "formula":
                continue
            for v in body.get("votes", []):
                if "p_accept" in v:
                    y = 1.0 if v["utility"] >= v["reservation"] else 0.0
                    errs.append((v["p_accept"] - y) ** 2)
    return round(statistics.fmean(errs), 4) if errs else 0.0


def feasible_real(wave, result: dict) -> bool:
    return rules.feasible(wave, plan_from_dict(result["final_plan"]))


def status_quo_feasible(wave, result: dict) -> bool:
    ev = result["baseline"]["evaluation"]
    if "feasible" in ev:
        return bool(ev["feasible"])
    return rules.feasible(wave, plan_from_dict(result["baseline"]["plan"]))


def summarize(runs: dict[str, dict], formula_runs: dict[str, dict], waves: list, common: set[str]) -> tuple[dict, dict]:
    """Returns (contract summary, extras for selection/markdown)."""
    N = len(waves)
    ok = [(wid, x["result"]) for wid, x in runs.items() if "result" in x]
    failed = sorted(wid for wid, x in runs.items() if "result" not in x)
    res = [r for _, r in ok]
    com = [r for wid, r in ok if wid in common]
    metas = [d for r in res for d in decisions(r)]
    model_lat = [d["latency_ms"] for d in metas if d["provider"] != "formula"]
    all_lat = [d["latency_ms"] for d in metas]
    errs = sum(1 for d in metas if d.get("fallback_from")) + len(failed)
    agree = [r["final_plan"]["id"] == formula_runs[wid]["result"]["final_plan"]["id"]
             for wid, r in ok if wid in common and wid in formula_runs and "result" in formula_runs[wid]]
    by_id = {w.id: w for w in waves}
    viol = sum(1 for wid, r in ok if not feasible_real(by_id[wid], r))
    s = {
        "consensus_rate_pct": pct(sum(r["outcome"] == "consensus" for r in res), N),
        "mean_rounds": mean([r["rounds"] for r in com]),
        "fallback_rate_pct": pct(sum(r["outcome"] == "fallback" for r in res), N),
        "rule_violations_in_agreed_plans": viol,
    }
    for k in TOTAL_KEYS:
        s[k] = mean([r["evaluation"]["totals"][k] for r in com])
    has_model = bool(model_lat)
    s.update({
        "agreement_with_formula_pct": pct(sum(agree), len(agree)),
        "brier": brier(res) if has_model else None,
        "latency_p50_ms": percentile(model_lat, 0.5) if has_model else None,
        "latency_p95_ms": percentile(model_lat, 0.95) if has_model else None,
        "error_rate_pct": pct(errs, len(metas) + len(failed)),
        "cost_usd_per_wave": 0.0,
    })
    return s, {"n_ok": len(ok), "failed_ids": failed, "p95_all_ms": percentile(all_lat, 0.95) if all_lat else None,
               "model_decisions": len(model_lat)}


def run_optimiser(waves: list, concurrency: int = 4) -> dict[str, dict]:
    """Centralised reference: deterministic beam search (fallback.solve), no models, no negotiation. Scored with
    the same evaluate() as the arms' totals."""
    from atm.wave.fallback import solve
    out: dict[str, dict] = {}

    def one(w):
        t0 = time.monotonic()
        try:
            plan, method = solve(w)
            ev = evaluate(w, plan)
            out[w.id] = {"delay_min": ev["totals"]["delay_min"], "fuel_kg": ev["totals"]["fuel_kg"],
                         "pax_protected_pct": ev["totals"]["pax_protected_pct"],
                         "feasible": bool(rules.feasible(w, plan)), "method": method,
                         "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}
        except Exception as e:  # noqa: BLE001
            out[w.id] = {"error": f"{type(e).__name__}: {e}"[:300]}
            log(f"[optimiser] {w.id}: EXCEPTION {e}")

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        list(ex.map(one, waves))
    return out


def optimiser_summary(opt: dict[str, dict], common: set[str]) -> dict:
    rows = [opt[w] for w in sorted(common) if "error" not in opt.get(w, {"error": 1})]
    d = [r["delay_min"] for r in rows]
    return {"n": len(rows), "delay_min": mean(d), "delay_min_ci95": ci95(d),
            "fuel_kg": mean([r["fuel_kg"] for r in rows]),
            "pax_protected_pct": mean([r["pax_protected_pct"] for r in rows]),
            "n_feasible": sum(r["feasible"] for r in rows),
            "elapsed_ms_mean": mean([r["elapsed_ms"] for r in rows]),
            "methods": dict(Counter(r["method"] for r in rows))}


def arm_vs_optimiser(arm_delay: dict[str, float], sq_delay: dict[str, float], sq_feasible: set[str],
                     opt: dict[str, dict], common: set[str]) -> dict:
    """arm_delay / sq_delay: wave id -> delay_min. gap over common waves; share over waves with a feasible status quo
    where the optimiser beats it."""
    ids = [w for w in sorted(common) if w in arm_delay and "error" not in opt.get(w, {"error": 1})]
    gap = [arm_delay[w] - opt[w]["delay_min"] for w in ids]
    sub = [w for w in ids if w in sq_feasible and opt[w]["delay_min"] < sq_delay[w]]
    share = None
    if sub:
        den = sum(sq_delay[w] - opt[w]["delay_min"] for w in sub)
        share = round(100.0 * sum(sq_delay[w] - arm_delay[w] for w in sub) / den, 2) if den else None
    return {"delay_min_ci95": ci95([arm_delay[w] for w in ids]),
            "gap_to_optimiser": mean(gap) if gap else None,
            "share_of_optimiser_gain_pct": share, "share_n": len(sub)}


def run_ablation(waves: list) -> float | None:
    unsafe = total = 0
    with engine_disabled():
        runs = run_arm("formula", waves, ["formula"], "formulas", 1, "ablation")
    by_id = {w.id: w for w in waves}
    for wid, x in runs.items():
        if "result" not in x:
            continue
        total += 1
        plan = plan_from_dict(x["result"]["final_plan"])
        if not evaluate(by_id[wid], plan)["feasible"]:  # real rules (patch already restored)
            unsafe += 1
    return pct(unsafe, total) if total else None


def select(arms: list[dict], n_total: int, baseline: dict) -> tuple[dict, dict | None]:
    """Returns (selection, best eligible model arm by the selection ordering or None). The outage test uses that
    arm even when the overall winner is formula."""
    rule = "best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%; tie -> lower Brier"
    reasons = ["Brier is measured against the formula verdict (y = 1 if utility >= reservation), not ground truth.",
               "cost_usd_per_wave = 0.0: output tokens free; input cost not measured.",
               "fallback_rate_pct = % of waves ending in the deterministic solver; error_rate_pct = % of decisions "
               "answered after this arm failed (a failed wave counts as one error decision)."]
    formula = next((a for a in arms if a["provider"] == "formula"), None)
    cands = [a for a in arms if a["provider"] != "formula"]
    for a in arms:
        if a["failed_ids"]:
            reasons.append(f"arm {a['label']}: {len(a['failed_ids'])}/{n_total} waves failed")
    if not cands:
        return {"winner": "formula", "rule": rule, "reasons": reasons + ["No model arms were run."]}, None

    def key(a):
        s = a["summary"]
        return (-s["delay_min"], s["pax_protected_pct"], -(s["brier"] or 0.0))

    eligible = []
    for a in cands:
        s = a["summary"]
        if a["n_ok"] < n_total:
            reasons.append(f"{a['label']} disqualified: only {a['n_ok']}/{n_total} waves succeeded")
        elif s["latency_p95_ms"] is None:
            reasons.append(f"{a['label']} disqualified: zero model-answered decisions")
        else:
            eligible.append(a)
            if a["p95_all_ms"] is not None:
                reasons.append(f"{a['label']} p95 all decisions incl. fallbacks: {a['p95_all_ms']} ms")
            if s["error_rate_pct"] >= ERR_LIMIT_PCT:
                reasons.append(f"{a['label']} missed error < 2%: {s['error_rate_pct']}%")
            if s["latency_p95_ms"] >= P95_LIMIT_MS:
                reasons.append(f"{a['label']} missed p95 < 1000 ms: {s['latency_p95_ms']} ms")
            if s["consensus_rate_pct"] < CONSENSUS_LIMIT_PCT:
                reasons.append(f"{a['label']} missed consensus >= 90%: {s['consensus_rate_pct']}%")
            nfb = round(s["fallback_rate_pct"] * n_total / 100)
            if nfb > 0:
                reasons.append(f"{a['label']} outcomes partly from solver fallback on {nfb}/{n_total} waves")
    if not eligible:
        return {"winner": "formula", "rule": rule,
                "reasons": ["No model arm was eligible; formula selected."] + reasons}, None
    ok = [a for a in eligible if a["summary"]["error_rate_pct"] < ERR_LIMIT_PCT
          and a["summary"]["latency_p95_ms"] < P95_LIMIT_MS
          and a["summary"]["consensus_rate_pct"] >= CONSENSUS_LIMIT_PCT]
    if ok:
        win = max(ok, key=key)
        lead = f"{win['label']} meets all three thresholds (error, p95, consensus) and has the best outcome among qualifying arms."
    else:
        win = max(eligible, key=key)
        lead = ("No arm met error < 2%, p95 < 1000 ms and consensus >= 90%: " + ", ".join(
            f"{a['label']} p95 {a['summary']['latency_p95_ms']} ms / error {a['summary']['error_rate_pct']}% / "
            f"consensus {a['summary']['consensus_rate_pct']}%"
            for a in eligible) + f". Picked best outcome among eligible model arms: {win['label']}.")
    reasons.insert(0, lead)
    winner = win["model"]
    if formula is not None:
        ws, fs = win["summary"], formula["summary"]
        if ws["delay_min"] > fs["delay_min"] and ws["pax_protected_pct"] <= fs["pax_protected_pct"]:
            reasons.insert(0, f"{win['label']} is worse than formula on delay ({ws['delay_min']} > {fs['delay_min']} min) "
                              "and not better on pax protected: formula selected.")
            winner = "formula"
        reasons.append(f"Mean delay: {win['label']} {ws['delay_min']} min vs formula {fs['delay_min']} min "
                       f"vs status quo {baseline['delay_min']} min.")
    return {"winner": winner, "rule": rule, "reasons": reasons}, win


def run_outage(winner_arm: str | None, waves: list, n: int, concurrency: int) -> tuple[dict, list[str]]:
    sub = waves[:n]
    out = {"primary_disabled": DEAD_MODEL.split(":", 1)[1], "waves": len(sub), "resolved": 0, "fallbacks_used": {}}
    if winner_arm is None:
        out["waves"] = 0
        return out, ["outage test skipped (no model arm with model-answered decisions)"]
    if not sub:
        return out, []
    by_id = {w.id: w for w in sub}
    runs = run_arm("outage", sub, [DEAD_MODEL, winner_arm, "formula"], "decision", concurrency, "outage")
    used: Counter = Counter()
    for wid, x in runs.items():
        if "result" not in x:
            continue
        if feasible_real(by_id[wid], x["result"]):
            out["resolved"] += 1
        for d in decisions(x["result"]):
            if d.get("fallback_from"):
                used[d["model"]] += 1
    out["fallbacks_used"] = dict(used)
    notes = [] if sum(used.values()) else ["outage test vacuous: dead primary never called"]
    return out, notes


# ---------------------------------------------------------------- failures / markdown

def collect_failures(label: str, runs: dict, waves: list) -> list[dict]:
    by_id = {w.id: w for w in waves}
    out = []
    for wid, x in runs.items():
        if "error" in x:
            out.append({"wave_id": wid, "model": label, "reason": f"exception: {x['error']}"})
            continue
        r = x["result"]
        if r["outcome"] == "fallback":
            out.append({"wave_id": wid, "model": label, "reason": "no consensus: deterministic solver fallback"})
        if not feasible_real(by_id[wid], r):
            out.append({"wave_id": wid, "model": label, "reason": "rule violation in final plan"})
        bd = r["baseline"]["evaluation"]["totals"]["delay_min"]
        nd = r["evaluation"]["totals"]["delay_min"]
        if status_quo_feasible(by_id[wid], r) and nd > bd:
            out.append({"wave_id": wid, "model": label,
                        "reason": f"regression vs status quo: delay {nd} > {bd} min"})
    return out


def _f(v) -> str:
    return "n/a" if v is None else str(v)


def markdown(rep: dict) -> str:
    L = [f"# Benchmark ({rep['n_waves']} waves, seed {rep['seed']}, source {rep.get('source', 'bts')})",
         f"Generated {rep['generated_at']}", "",
         "Outcome means (delay, fuel, pax, overtime, rounds, agreement) are over waves that succeeded in every arm "
         f"({rep['n_common']} of {rep['n_waves']}).", "",
         "## Arms vs status quo", "",
         "| arm | ok waves | consensus % | rounds | fallback % | violations | delay min | fuel kg | pax prot % | overtime min "
         "| agree w/ formula % | Brier | p50 ms | p95 ms | error % |", "|" + "---|" * 15]
    b = rep["baseline"]
    L.append(f"| status quo | - | - | - | - | - | {b['delay_min']} | {b['fuel_kg']} | {b['pax_protected_pct']} "
             f"| {b['overtime_min']} | - | - | - | - | - |")
    for a in rep["arms"]:
        s = a["summary"]
        L.append(f"| {a['label']} | {a['n_ok']}/{rep['n_waves']} | {s['consensus_rate_pct']} | {s['mean_rounds']} "
                 f"| {s['fallback_rate_pct']} | {s['rule_violations_in_agreed_plans']} | {s['delay_min']} | {s['fuel_kg']} "
                 f"| {s['pax_protected_pct']} | {s['overtime_min']} | {s['agreement_with_formula_pct']} | {_f(s['brier'])} "
                 f"| {_f(s['latency_p50_ms'])} | {_f(s['latency_p95_ms'])} | {s['error_rate_pct']} |")
    op = rep.get("optimiser")
    if op:
        L.insert(L.index(f"| status quo | - | - | - | - | - | {b['delay_min']} | {b['fuel_kg']} | {b['pax_protected_pct']} "
                         f"| {b['overtime_min']} | - | - | - | - | - |") + 1,
                 f"| Optimiser (centralised beam search, no negotiation) | - | - | - | - | {op['n'] - op['n_feasible']} "
                 f"| {op['delay_min']} | {op['fuel_kg']} | {op['pax_protected_pct']} | - | - | - | - | - | - |")
    fo = b["feasible_only"]
    L += ["", f"Status quo infeasible on {len(b['infeasible_ids'])} waves: {', '.join(b['infeasible_ids']) or 'none'}.", ""]
    L += [f"- {a['label']} vs status quo on {b['n_feasible']} feasible-status-quo waves: delay "
          f"{a['delay_min_vs_feasible_status_quo']} min vs {fo['delay_min']} min" for a in rep["arms"]]
    if op:
        ci = lambda c: "n/a" if c is None else f"[{c[0]}, {c[1]}]"  # noqa: E731
        L += ["", f"Optimiser reference (n={op['n']}, {op['n_feasible']} feasible): mean delay {op['delay_min']} min, "
                  f"95% CI {ci(op['delay_min_ci95'])}; no models, no negotiation."]
        for a in rep["arms"]:
            sh = a.get("share_of_optimiser_gain_pct")
            L.append(f"- {a['label']}: delay 95% CI {ci(a.get('delay_min_ci95'))}; gap to optimiser "
                     f"{_f(a.get('gap_to_optimiser'))} min; share of optimiser gain over status quo "
                     f"{'n/a' if sh is None else f'{sh}%'} (n={a.get('share_n', 0)} waves with feasible status quo "
                     "beaten by the optimiser).")
    sel = rep["selection"]
    o = rep["outage_test"]
    ab = rep["ablation_no_engine_unsafe_pct"]
    L += ["", f"Status-quo diversions (mean): {b['diversions']}", "", "## Selection", "",
          f"**Winner:** {sel['winner']}", "", f"Rule: {sel['rule']}", ""] + [f"- {r}" for r in sel["reasons"]]
    L += ["", "## Outage test", "",
          f"Primary `{o['primary_disabled']}` disabled on {o['waves']} wave(s): {o['resolved']} resolved. "
          f"Fallbacks used: {json.dumps(o['fallbacks_used'])}", "",
          "## Ablation: safety engine off", "",
          "not run" if ab is None else f"{ab}% of formula-arm final plans are infeasible under the real rules "
          "when the engine is disabled.", "", "## Failures", ""]
    L += [f"- `{f['wave_id']}` ({f['model']}): {f['reason']}" for f in rep["failures"]] or ["- none"]
    L += ["", "## Limits", "",
          "- Several data layers are simulated (gates, fuel, crew, connections, aircraft types), see per-wave provenance.",
          "- Brier is against the formula verdict (utility >= reservation), not human judgement.",
          f"- n = {rep['n_waves']} waves: differences between arms are indicative, not statistically significant.",
          "- Cost is not measured (0.0)."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--arms", default="formula")
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--outage-n", type=int, default=20)
    ap.add_argument("--ablation", action="store_true", help="(default on)")
    ap.add_argument("--no-ablation", action="store_true")
    ap.add_argument("--source", choices=("bts", "flights2015"), default="flights2015")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "eval"))
    a = ap.parse_args(argv)

    arm_list = [x.strip() for x in a.arms.split(",") if x.strip()]
    if "formula" not in arm_list:
        arm_list.append("formula")  # needed for agreement_with_formula_pct
    waves, build_fail = load_waves(a.n, a.seed, a.fixtures, a.source)
    log(f"{len(waves)} waves, arms={arm_list}")

    all_runs: dict[str, dict] = {}
    for arm in arm_list:
        f = arm == "formula"
        all_runs[arm] = run_arm(arm, waves, ["formula"] if f else [arm, "formula"],
                                "formulas" if f else "decision", a.concurrency, arm)
    formula_runs = all_runs["formula"]
    common = {w.id for w in waves if all("result" in all_runs[arm].get(w.id, {}) for arm in arm_list)}

    by_id = {w.id: w for w in waves}
    sq_ok = sorted(w for w in common if status_quo_feasible(by_id[w], formula_runs[w]["result"]))
    sq_bad = sorted(common - set(sq_ok))
    keys = (*TOTAL_KEYS, "diversions")
    ok_f = [formula_runs[w]["result"] for w in common]
    sq_f = [formula_runs[w]["result"] for w in sq_ok]
    base = {k: mean([r["baseline"]["evaluation"]["totals"][k] for r in ok_f]) for k in keys}
    base["n_feasible"] = len(sq_ok)
    base["infeasible_ids"] = sq_bad
    base["feasible_only"] = {k: mean([r["baseline"]["evaluation"]["totals"][k] for r in sq_f]) for k in keys}

    opt = run_optimiser(waves, a.concurrency)
    optimiser = optimiser_summary(opt, common)
    sq_delay = {w: formula_runs[w]["result"]["baseline"]["evaluation"]["totals"]["delay_min"] for w in common}
    arms, failures = [], list(build_fail)
    for arm in arm_list:
        prov, model = arm_parts(arm)
        summ, extra = summarize(all_runs[arm], formula_runs, waves, common)
        arm_delay = mean([all_runs[arm][w]["result"]["evaluation"]["totals"]["delay_min"] for w in sq_ok])
        arms.append({"model": model, "provider": prov, "label": model, "summary": summ,
                     "delay_min_vs_feasible_status_quo": arm_delay, **extra,
                     **arm_vs_optimiser({w: all_runs[arm][w]["result"]["evaluation"]["totals"]["delay_min"]
                                         for w in common}, sq_delay, set(sq_ok), opt, common)})
        failures += collect_failures(model, all_runs[arm], waves)

    selection, best_model = select(arms, len(waves), base)
    if len(waves) < a.n:
        selection["reasons"].append(f"loaded {len(waves)} of {a.n} requested waves")
    outage_arm = next((x for x in arm_list if best_model and x != "formula" and arm_parts(x)[1] == best_model["model"]),
                      None)
    outage, notes = run_outage(outage_arm, waves, a.outage_n, a.concurrency)
    if outage_arm and selection["winner"] == "formula":
        selection["reasons"].append(f"Outage test ran with the best eligible model arm {best_model['label']} "
                                    "(overall winner is formula).")
    selection["reasons"] += notes
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_waves": len(waves), "n_common": len(common), "seed": a.seed,
        "source": "fixtures" if a.fixtures else a.source, "arms": arms, "baseline": base,
        "optimiser": optimiser,
        "ablation_no_engine_unsafe_pct": None if a.no_ablation else run_ablation(waves),
        "outage_test": outage,
        "selection": selection, "failures": failures, "wave_ids": [w.id for w in waves],
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(report, indent=2))
    (out / "BENCHMARK.md").write_text(markdown(report))
    log(f"wrote {out}/benchmark.json and BENCHMARK.md")
    return report


if __name__ == "__main__":
    main()
