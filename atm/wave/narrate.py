"""Claude narration of a finished wave negotiation (spec §9). Claude explains only: it receives the
structured outcome and writes a duty-manager brief plus a plain-English transcript. It decides
nothing. Any failure (no ANTHROPIC_API_KEY, SDK error, refusal, bad output) -> deterministic template,
with an extra "error" key that graph.negotiate_wave moves into model_errors."""
from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

MODEL = os.environ.get("NARRATION_MODEL", "claude-haiku-5-5")  # fast and cheap; it only explains
MAX_WORDS = 150
MAX_LINES = 40
KEY_TYPES = ("TRIAGE", "OPEN", "AGENT_TURN", "CHECK", "CONSENSUS", "NO_CONSENSUS", "FALLBACK",
             "MODEL_FALLBACK")


class Narration(BaseModel):
    brief: str = Field(description="At most 110 words for the hub duty manager: what was agreed, why, "
                                   "risks, and what needs their approval.")
    lines: list[str] = Field(description="Plain-English transcript, one short line per key message, in order.")


def _trim(text: str, n: int = MAX_WORDS) -> str:
    text = text.replace(" — ", ", ").replace("—", ", ")  # house style: no em dashes in the brief
    words = text.split()
    if len(words) <= n:
        return text.strip()
    cut = " ".join(words[:n])  # end on the last full sentence, never mid-sentence
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 0 else cut + " …"


def _key_messages(result: dict) -> list[dict]:
    out = []
    for m in result.get("log") or []:
        if m["type"] not in KEY_TYPES:
            continue
        if m["type"] == "CHECK" and m["body"].get("passed"):
            continue
        out.append(m)
    return out[:MAX_LINES]


def _compact(result: dict) -> dict:
    ev, base = result["evaluation"], result["baseline"]["evaluation"]
    failed = [c for c in base.get("checks", []) if not c["passed"]]
    return {
        "wave_id": result["wave_id"], "outcome": result["outcome"], "turns": result.get("turns", result["rounds"]),
        "agents": result.get("agents"), "revision": (result.get("revision") or {}).get("summary"),
        "final_plan": result["final_plan"]["label"],
        "assignments": [{k: a.get(k) for k in ("callsign", "action", "runway", "slot_min", "gate",
                                                "speed_reduction_min", "ground_delay_min")}
                        for a in result["final_plan"]["assignments"]],
        "totals": ev["totals"], "baseline_totals": base["totals"],
        "utilities": ev["utilities"],
        "rule_checks": {"final_all_passed": ev["feasible"], "final_count": len(ev["checks"]),
                        "baseline_violations": [f"{c['rule']} {c.get('callsign') or ''}: {c['detail']}"[:160]
                                                for c in failed][:8]},
        "model_fallbacks": result["stats"].get("fallbacks", 0),
        "model_errors": result.get("model_errors", [])[:3],
        "key_messages": [f"T{m['round']} {m['sender']} {m['type']}: {m['rationale']}"[:200]
                         for m in _key_messages(result)],
    }


def _template(result: dict) -> dict:
    ev, base = result["evaluation"], result["baseline"]["evaluation"]
    t, b = ev["totals"], base["totals"]
    how = (f"ATC, pilot and ground agreed in turn {result['rounds']}" if result["outcome"] == "consensus"
           else f"No agreement after {result['rounds']} turns; the deterministic solver chose the plan")
    rev = (result.get("revision") or {}).get("summary")
    if rev:
        how = f"{rev}. {how}"
    base_v = sum(not c["passed"] for c in base.get("checks", []))
    risks = []
    if t["diversions"]:
        risks.append(f"{t['diversions']} diversion(s)")
    if t["overtime_min"]:
        risks.append(f"{t['overtime_min']:g} min crew overtime")
    if result["stats"].get("fallbacks"):
        risks.append(f"{result['stats']['fallbacks']} decision-model fallback(s)")
    brief = (f"{how}: {result['final_plan']['label']}. All {len(ev['checks'])} safety checks pass. "
             f"Delay {t['delay_min']:g} min vs {b['delay_min']:g} baseline, holding fuel {t['fuel_kg']:g} kg vs "
             f"{b['fuel_kg']:g}, connections protected {t['pax_protected_pct']:g}% vs {b['pax_protected_pct']:g}%. "
             f"Status quo had {base_v} rule violation(s). Risks: {', '.join(risks) or 'none flagged'}. "
             "Approve or override the plan.")
    lines = [f"Turn {m['round']}, {m['sender']} ({m['type'].lower().replace('_', ' ')}): {m['rationale']}"
             for m in _key_messages(result)]
    return {"source": "template", "brief": _trim(brief), "lines": lines}


def _failed(result: dict, e: Exception | str) -> dict:
    out = _template(result)
    out["error"] = (e if isinstance(e, str) else f"{type(e).__name__}: {e}")[:200]
    return out


def brief(result: dict) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _failed(result, "ANTHROPIC_API_KEY not set")
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=20.0, max_retries=1)
        payload = json.dumps(_compact(result), separators=(",", ":"), default=str)
        prompt = ("You narrate the outcome of an automated arrival-wave disruption negotiation between ATC, "
                  "pilot and ground agents at an airline hub. A deterministic safety engine checked every "
                  "plan; you explain, you do not decide or re-judge legality. Use only the facts below.\n"
                  f"Write `brief` (at most 110 words, for the hub duty manager: what was agreed, why, "
                  "risks, what needs their approval; compare with the status-quo baseline honestly, including "
                  "anything that got worse) and `lines` (plain English, one line per key message, in order).\n\n"
                  f"DATA: {payload}")
        resp = client.messages.parse(model=MODEL, max_tokens=1500, output_config={"effort": "low"},
                                     output_format=Narration, messages=[{"role": "user", "content": prompt}])
        out = resp.parsed_output
        if resp.stop_reason == "refusal" or out is None or not out.brief.strip():
            raise RuntimeError(f"no structured output ({resp.stop_reason})")
        return {"source": "claude", "model": getattr(resp, "model", None) or MODEL, "brief": _trim(out.brief),
                "lines": [ln.strip() for ln in out.lines if ln.strip()][:MAX_LINES]}
    except Exception as e:  # noqa: BLE001 - any failure falls back to the template, never breaks the run
        return _failed(result, e)
