"""Stand-in for atm.wave.build / atm.wave.graph over tests/fixtures.make_wave(); same signatures.
Used by atm.server only when the real modules are not importable."""
from __future__ import annotations

import random as _random
import time
from datetime import datetime, timezone
from pathlib import Path

from .model import Assignment, FlightMetrics, JointPlan, Move, RuleCheck, Wave

WAVE_ID = "ATL-2024-07-15-16"
_REPLAY_DIR = Path(__file__).resolve().parents[2] / "data" / "replay"


def _fixture_wave() -> Wave:
    from .fixtures import make_wave
    return make_wave()


def _summary(w: Wave) -> dict:
    a = w.airport
    return {"id": w.id, "airport": {"iata": a.iata, "icao": a.icao, "name": a.name},
            "window_start": w.window_start, "window_end": "2024-07-15T17:00:00",
            "n_flights": len(w.flights), "n_queue": len(w.queue),
            "callsigns": [f.callsign for f in w.flights],
            "disrupted_callsigns": [f.callsign for f in w.flights if f.disrupted],
            "weather_summary": (w.metar.raw if w.metar else ""),
            "replay_available": (_REPLAY_DIR / f"{w.id}.json").exists()}


def list_waves(airport: str | None = None, q: str | None = None, limit: int = 50) -> list[dict]:
    s = _summary(_fixture_wave())
    if airport and airport.upper() not in (s["airport"]["iata"], s["airport"]["icao"]):
        return []
    if q and q.upper() not in [c.upper() for c in s["callsigns"]] and q.upper() not in s["id"].upper():
        return []
    return [s][:limit]


def random_wave(airport: str | None = None, seed: int | None = None) -> dict:
    waves = list_waves(airport)
    if not waves:
        raise KeyError(airport)
    return _random.Random(seed).choice(waves)


def build_wave(wave_id: str) -> Wave:
    if wave_id != WAVE_ID:
        raise KeyError(wave_id)
    return _fixture_wave()


def _plan(wave: Wave, shift: int = 0, label: str = "") -> JointPlan:
    asg = []
    for i, f in enumerate(wave.flights):
        slot = f.sched_min + 3 * i + shift
        asg.append(Assignment(
            f.callsign, "land", "26R" if i % 2 == 0 else "27L", slot, f.scheduled_gate,
            metrics=FlightMetrics(hold_min=max(0, slot - f.sched_min), fuel_kg=100.0 * i,
                                  delay_min=max(0, slot - f.sched_min), in_block_min=slot + 6,
                                  pax_protected=12, pax_total=12)))
    return JointPlan(asg, label)


def _eval(plan: JointPlan, feasible: bool = True) -> dict:
    return {"plan_id": plan.id, "feasible": feasible,
            "checks": [RuleCheck("ATC-RWY-OPEN", "ATC", feasible, "runway open (stub)", "ops rule",
                                 {"runway": "26R"}, plan.assignments[0].callsign).to_dict()],
            "utilities": {"ATC": 0.7, "GROUND": 0.6, **{f"PILOT:{a.callsign}": 0.65 for a in plan.assignments}},
            "totals": {"delay_min": sum(a.metrics.delay_min for a in plan.assignments), "fuel_kg": 600.0,
                       "hold_min": 10.0, "pax_protected_pct": 100.0, "overtime_min": 0.0, "diversions": 0}}


def negotiate_wave(wave: Wave, mode: str, chain: list[str] | None = None, on_message=None) -> dict:
    t0 = time.time()
    chain = chain or ["formula"]
    model = chain[0]
    provider = "formula" if model == "formula" else model.split(":", 1)[0]
    log: list[dict] = []

    def emit(rnd, sender, typ, body, rationale, dec=True):
        msg = {"seq": len(log), "round": rnd, "ts": datetime.now(timezone.utc).isoformat(),
               "sender": sender, "type": typ, "body": {"type": typ, **body}, "rationale": rationale}
        if dec:
            msg["decision"] = {"kind": "formula", "model": "formula", "provider": "formula", "latency_ms": 1}
        log.append(msg)
        if on_message:
            on_message(msg)
        time.sleep(0.05)

    base = _plan(wave)
    final = _plan(wave, shift=1)
    cs = wave.flights[0].callsign
    mv = Move("m1", "ATC", "shift_slot", [cs], {"minutes": 1}, f"Shift {cs} slot +1")
    bad = Move("m2", f"PILOT:{cs}", "speed_reduction", [cs], {"minutes": 9}, f"{cs} slow 9 min")
    emit(0, "DECIDER", "TRIAGE", {"score": 0.6, "levels": ["low", "medium", "high"],
                                  "probabilities": [0.1, 0.3, 0.6]}, "Disrupted heavy arrival; high severity.")
    emit(0, "ATC", "OPEN", {"plan": base.to_dict(), "evaluation": _eval(base)}, "FCFS opening plan.")
    emit(1, "ATC", "MOVE", {"move": mv.__dict__, "plan": final.to_dict(), "options_count": 4}, "Smooths the queue.")
    emit(1, f"PILOT:{cs}", "MOVE_REJECTED",
         {"move": bad.__dict__, "violations": [RuleCheck("PLT-EARLIEST-ETA", "PILOT", False,
                                                         "speed reduction 9 > 8 min cap", "ops rule",
                                                         {"minutes": 9, "limit": 8}, cs).to_dict()]},
         "Engine vetoed an out-of-range speed reduction.", dec=False)
    emit(1, "GROUND", "COUNTER", {"plan": final.to_dict(), "evaluation": _eval(final)}, "Keeps gates as scheduled.")
    emit(1, "ENGINE", "CHECK", {"plan_id": final.id, "passed": True, "violations": []}, "All rules pass.", dec=False)
    emit(1, "ATC", "VOTE", {"votes": [{"plan_id": final.id, "decision": "ACCEPT", "utility": 0.7,
                                       "reservation": 0.5, "reason": "Above reservation."}]}, "Accepts.")
    emit(1, "ENGINE", "CONSENSUS", {"plan_id": final.id, "accepted_by": ["ATC", "GROUND"]}, "All agents accept.",
         dec=False)
    return {"run_id": "", "wave_id": wave.id, "mode": mode, "model": model, "outcome": "consensus", "rounds": 1,
            "final_plan": final.to_dict(), "evaluation": _eval(final),
            "baseline": {"plan": base.to_dict(), "evaluation": _eval(base)},
            "narration": None, "model_errors": [],
            "stats": {"model_calls": 0, "fallbacks": 0, "elapsed_ms": int((time.time() - t0) * 1000)},
            "log": log}
