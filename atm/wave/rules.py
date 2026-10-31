"""Deterministic safety/legality engine for joint wave plans.

No model ever decides legality: every check below is a formula over the plan and the wave's
data, and every RuleCheck cites its rule, source and the input values used.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from atm.wave import wind
from atm.wave.model import FlightMetrics, JointPlan, RuleCheck, Wave
from atm.wave.utility import MAX_SPEED_REDUCTION_MIN, TURNAROUND_MIN, fuel_info, metrics, taxi_min

SRC_WAKE = "ICAO Doc 4444 wake turbulence separation (time-based approximation)"
SRC_FUEL = "14 CFR 121.639 reserve + alternate"
SRC_OPS = "ops rule"
SRC_RWY = "airport runway status"
WET_VIS_SM = 3.0
WEATHER_FACTOR_BAD = 1.25
_RWY_RE = re.compile(r"^(\d{1,2})([LRC]?)$")


def wake_gap_min(leader: str, follower: str) -> float:
    """Minimum time (min) between a leader and a follower landing on the same runway (dry)."""
    if leader == "SUPER":
        return 4.0
    if leader == "HEAVY" and follower in ("LARGE", "B757", "SMALL"):
        return 3.0
    if leader == "B757" and follower in ("LARGE", "SMALL"):
        return 2.5
    return 2.0


def weather_factor(wave: Wave) -> float:
    m = wave.metar
    if m is None:
        return 1.0
    if m.surface == "wet" or (m.visibility_sm is not None and m.visibility_sm < WET_VIS_SM):
        return WEATHER_FACTOR_BAD
    return 1.0


def reciprocal(runway_id: str) -> str | None:
    """Opposite end of a runway designator: 08L -> 26R, 09C -> 27C, 36 -> 18. None if unparsable."""
    m = _RWY_RE.match(runway_id or "")
    if not m:
        return None
    num = (int(m.group(1)) + 18 - 1) % 36 + 1
    side = {"L": "R", "R": "L"}.get(m.group(2), m.group(2))
    return f"{num:02d}{side}"


def strip_of(runway_id: str) -> tuple[str, ...]:
    """Physical runway (strip) key: sorted tuple of both ends, e.g. ('08L', '26R')."""
    rec = reciprocal(runway_id)
    return tuple(sorted({runway_id, rec})) if rec else (runway_id,)


def strip_label(runway_id: str) -> str:
    return "/".join(strip_of(runway_id))


def has_slot(a) -> bool:
    return a.slot_min is not None and isinstance(a.slot_min, (int, float)) and math.isfinite(a.slot_min)


def validate_structure(wave: Wave, plan: JointPlan) -> None:
    """Raise ValueError unless the plan has exactly one assignment per wave flight."""
    want = [f.callsign for f in wave.flights]
    got = [a.callsign for a in plan.assignments]
    cnt = Counter(got)
    missing = sorted(set(want) - set(got))
    unknown = sorted(set(got) - set(want))
    dup = sorted(c for c, n in cnt.items() if n > 1)
    if missing or unknown or dup:
        raise ValueError(f"plan callsigns must match wave flights exactly: missing={missing} "
                         f"unknown={unknown} dup={dup}")


def strip_sequence(wave: Wave, plan: JointPlan, runway: str) -> list[tuple[float, str, str, bool, str]]:
    """All landings on the strip of `runway` (both ends, queue included) as
    (slot, callsign, wake, is_wave_flight, runway_end), sorted by time (ties: queue first, callsign)."""
    key = strip_of(runway)
    seq = [(float(q.slot_min), q.callsign, q.wake, False, q.runway) for q in wave.queue
           if strip_of(q.runway) == key]
    wake = {f.callsign: f.wake for f in wave.flights}
    for a in plan.assignments:
        if a.action == "land" and a.runway and has_slot(a) and strip_of(a.runway) == key:
            seq.append((float(a.slot_min), a.callsign, wake.get(a.callsign, "LARGE"), True, a.runway))
    seq.sort(key=lambda x: (x[0], x[3], x[1]))
    return seq


def runway_sequence(wave: Wave, plan: JointPlan, runway: str) -> list[tuple[float, str, str, bool]]:
    """Landings on the strip of `runway` as (slot, callsign, wake, is_wave_flight)."""
    return [x[:4] for x in strip_sequence(wave, plan, runway)]


def direction_conflicts(wave: Wave, plan: JointPlan) -> dict[str, str]:
    """callsign -> reason for wave flights landing on the non-dominant end of a strip that is also
    used in the opposite direction. Dominant end: the queue's end if the (fixed) queue uses exactly
    one end of the strip; otherwise the end with the most landings (ties: lower runway id)."""
    by_strip: dict[tuple, list[tuple[str, str, bool]]] = {}
    for q in wave.queue:
        by_strip.setdefault(strip_of(q.runway), []).append((q.runway, q.callsign, False))
    for a in plan.assignments:
        if a.action == "land" and a.runway:
            by_strip.setdefault(strip_of(a.runway), []).append((a.runway, a.callsign, True))
    out: dict[str, str] = {}
    for key, uses in by_strip.items():
        ends = {u[0] for u in uses}
        if len(ends) < 2:
            continue
        q_ends = {u[0] for u in uses if not u[2]}
        if len(q_ends) == 1:
            dom = next(iter(q_ends))
        else:
            n = Counter(u[0] for u in uses)
            dom = min(ends, key=lambda e: (-n[e], e))
        ex = next(u for u in uses if u[0] == dom)
        who = f"{'wave' if ex[2] else 'queue'} landing {ex[1]} {dom}"
        for end, cs, is_wave in uses:
            if is_wave and end != dom:
                out[cs] = f"opposite-direction use of strip {'/'.join(key)} ({who})"
    return out


def _wake_checks(wave: Wave, plan: JointPlan) -> dict[str, RuleCheck]:
    fac = weather_factor(wave)
    out: dict[str, RuleCheck] = {}
    strips = {strip_of(a.runway): a.runway for a in plan.assignments
              if a.action == "land" and a.runway and has_slot(a)}
    for key, rw in strips.items():
        seq = strip_sequence(wave, plan, rw)
        sl = "/".join(key)
        for i, (t, cs, wk, is_wave, end) in enumerate(seq):
            if not is_wave:
                continue
            problems, ev = [], {"runway": end, "strip": sl, "slot_min": t, "wake": wk, "weather_factor": fac}
            if i > 0:
                lt, lcs, lwk, _, lend = seq[i - 1]
                need = round(wake_gap_min(lwk, wk) * fac, 2)
                gap = round(t - lt, 2)
                ev.update({"leader": lcs, "leader_wake": lwk, "leader_runway": lend,
                           "gap_behind_min": gap, "required_behind_min": need})
                if gap < need:
                    problems.append(f"{gap:g} min behind {lcs} ({lwk}, {lend}), need {need:g}")
            if i + 1 < len(seq) and not seq[i + 1][3]:
                # follower is a fixed queue arrival: this wave flight owns that gap
                ft, fcs, fwk, _, fend = seq[i + 1]
                need = round(wake_gap_min(wk, fwk) * fac, 2)
                gap = round(ft - t, 2)
                ev.update({"follower": fcs, "follower_wake": fwk, "follower_runway": fend,
                           "gap_ahead_min": gap, "required_ahead_min": need})
                if gap < need:
                    problems.append(f"{gap:g} min ahead of queue arrival {fcs} ({fwk}, {fend}), need {need:g}")
            where = f"{cs} on {end} (strip {sl}) at T{t:+g}"
            detail = (f"{where}: " + ("; ".join(problems) if problems else "wake separation met")
                      + f" (weather factor x{fac:g})")
            out[cs] = RuleCheck("ATC-WAKE-SEP", "ATC", not problems, detail, SRC_WAKE, ev, cs)
    return out


def _gate_free_checks(wave: Wave, plan: JointPlan, mm: dict[str, FlightMetrics]) -> dict[str, RuleCheck]:
    out: dict[str, RuleCheck] = {}
    gates = {g.id: g for g in wave.gates}
    landing = [a for a in plan.assignments if a.action == "land"]
    for a in landing:
        g = gates.get(a.gate or "")
        ib = mm[a.callsign].in_block_min
        if g is None or ib is None or not math.isfinite(ib):
            out[a.callsign] = RuleCheck("GND-GATE-FREE", "GROUND", False,
                                        f"{a.callsign}: unknown gate {a.gate!r} or no in-block time", SRC_OPS,
                                        {"gate": str(a.gate)}, a.callsign)
            continue
        end = ib + TURNAROUND_MIN
        ev = {"gate": g.id, "need_from_min": ib, "need_to_min": round(end, 1)}
        if g.remote:
            out[a.callsign] = RuleCheck("GND-GATE-FREE", "GROUND", True,
                                        f"Remote stand {g.id} has unlimited positions", SRC_OPS, ev, a.callsign)
            continue
        clash = None
        for b in g.busy:
            if b["from_min"] < end and ib < b["to_min"]:
                clash = (b.get("label", "occupied"), b["from_min"], b["to_min"])
                break
        if clash is None:
            for o in landing:
                if o is a or o.gate != g.id:
                    continue
                oib = mm[o.callsign].in_block_min
                if oib is not None and oib < end and ib < oib + TURNAROUND_MIN:
                    clash = (o.callsign, oib, oib + TURNAROUND_MIN)
                    break
        if clash:
            ev.update({"conflict": str(clash[0]), "conflict_from_min": clash[1], "conflict_to_min": round(clash[2], 1)})
            detail = (f"Gate {g.id} occupied by {clash[0]} T{clash[1]:+g}..T{clash[2]:+g}, "
                      f"{a.callsign} needs T{ib:+g}..T{end:+g}")
        else:
            detail = f"Gate {g.id} free T{ib:+g}..T{end:+g}"
        out[a.callsign] = RuleCheck("GND-GATE-FREE", "GROUND", clash is None, detail, SRC_OPS, ev, a.callsign)
    return out


def _divert_check(f, cs: str) -> RuleCheck:
    extra, alt, alt_min, fin = fuel_info(f)
    ev = {"alternate": str(alt) if alt else "missing",
          "alternate_min": alt_min if alt_min is not None else "missing", "final_reserve_min": fin}
    if not alt:
        return RuleCheck("PLT-FUEL-RESERVE", "PILOT", False,
                         f"{cs} cannot divert: no alternate aerodrome in fuel data", SRC_FUEL, ev, cs)
    if alt_min is None or alt_min <= 0:
        return RuleCheck("PLT-FUEL-RESERVE", "PILOT", False,
                         f"{cs} cannot divert to {alt}: alternate fuel time "
                         f"{'missing' if alt_min is None else f'{alt_min:g} min'} is not a valid planned value",
                         SRC_FUEL, ev, cs)
    return RuleCheck("PLT-FUEL-RESERVE", "PILOT", True,
                     f"{cs} diverts to {alt} ({alt_min:g} min), covered by alternate fuel + {fin:g} min final reserve",
                     SRC_FUEL, ev, cs)


def check(wave: Wave, plan: JointPlan, mm: dict[str, FlightMetrics] | None = None) -> list[RuleCheck]:
    """All rules for all flights. Raises ValueError if the plan does not have exactly one
    assignment per wave flight."""
    validate_structure(wave, plan)
    mm = mm or {a.callsign: metrics(wave, a) for a in plan.assignments}
    rwys = {r.id: r for r in wave.airport.runways}
    gates = {g.id: g for g in wave.gates}
    flights = {f.callsign: f for f in wave.flights}
    wake = _wake_checks(wave, plan)
    gfree = _gate_free_checks(wave, plan, mm)
    opposite = direction_conflicts(wave, plan)
    metar = wave.metar
    out: list[RuleCheck] = []
    for a in plan.assignments:
        f = flights[a.callsign]
        m = mm[a.callsign]
        cs = a.callsign
        if a.action == "divert":
            out.append(_divert_check(f, cs))
            continue

        # ATC-RWY-OPEN (exists, open, not used against the flow on the same strip)
        rw = rwys.get(a.runway or "")
        if rw is None:
            out.append(RuleCheck("ATC-RWY-OPEN", "ATC", False, f"{cs}: runway {a.runway!r} does not exist at "
                                 f"{wave.airport.icao}", SRC_RWY, {"runway": str(a.runway)}, cs))
        else:
            probs = []
            if not rw.open:
                probs.append(f"closed ({rw.closed_reason or 'no reason given'})")
            if cs in opposite:
                probs.append(opposite[cs])
            ev = {"runway": rw.id, "open": "yes" if rw.open else "no", "strip": strip_label(rw.id)}
            if cs in opposite:
                ev["direction_conflict"] = opposite[cs]
            out.append(RuleCheck("ATC-RWY-OPEN", "ATC", not probs,
                                 f"{cs}: runway {rw.id} " + ("; ".join(probs) if probs else "open, landing with the flow"),
                                 SRC_RWY, ev, cs))

        # ATC-WAKE-SEP
        out.append(wake.get(cs) or RuleCheck("ATC-WAKE-SEP", "ATC", False, f"{cs}: no valid slot assigned",
                                             SRC_WAKE, {"runway": str(a.runway), "slot_min": str(a.slot_min)}, cs))

        # ATC-TAXI-ROUTE (no taxiway closures modelled in wave data: informational pass)
        g = gates.get(a.gate or "")
        tx = taxi_min(wave, a.runway, a.gate)
        dest = "remote stand" if g is not None and g.remote else "pier " + (g.pier if g else "?")
        out.append(RuleCheck("ATC-TAXI-ROUTE", "ATC", True,
                             f"{cs}: taxi route {a.runway} -> {dest} available ({tx:g} min); no taxiway closures in data",
                             SRC_OPS, {"runway": str(a.runway), "gate": str(a.gate), "taxi_min": tx}, cs))

        # ATC-WIND-LIMITS
        if metar is None:
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", True, f"{cs}: no METAR available, wind not assessed",
                                 "FAA Order 8400.9 §6d", {"metar": "unavailable"}, cs))
        elif rw is not None:
            hw, xw, tw = wind.components(metar.wind_dir_deg, metar.wind_kt, metar.gust_kt, rw.heading_true)
            lxw, ltw, src = wind.limits(metar.surface, f.aircraft_type)
            ok = wind.within_limits(xw, tw, lxw, ltw)
            ev = {"metar": metar.raw, "runway": rw.id, "surface": metar.surface, "headwind_kt": hw,
                  "crosswind_kt": xw, "tailwind_kt": tw, "limit_crosswind_kt": lxw, "limit_tailwind_kt": ltw,
                  "aircraft_type": f.aircraft_type or "unknown"}
            why = []
            if xw > lxw:
                why.append(f"crosswind {xw:g} kt > limit {lxw:g} kt")
            if tw > (3.0 if ltw == 0 else ltw):  # wind.within_limits: calm 0-3 kt is not a tailwind
                why.append(f"tailwind {tw:g} kt > limit {ltw:g} kt")
            detail = (f"{cs} ({f.aircraft_type or 'unknown type'}) on {rw.id}: "
                      + ("; ".join(why) if not ok else f"crosswind {xw:g}/{lxw:g} kt, tailwind {tw:g}/{ltw:g} kt within limits")
                      + f" [METAR {metar.raw}]")
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", ok, detail, src, ev, cs))
        else:
            out.append(RuleCheck("ATC-WIND-LIMITS", "ATC", False, f"{cs}: unknown runway {a.runway!r}",
                                 "FAA Order 8400.9 §6d", {"metar": metar.raw, "runway": str(a.runway)}, cs))

        # PLT-EARLIEST-ETA (incl. absorption bounds)
        slot_ok = has_slot(a)
        slot = float(a.slot_min) if slot_ok else float("nan")
        earliest = f.eta_earliest_min + a.speed_reduction_min + a.ground_delay_min
        problems = []
        if not slot_ok:
            problems.append(f"no valid slot ({a.slot_min!r})")
        elif slot < earliest:
            problems.append(f"slot T{slot:+g} before earliest achievable T{earliest:+g} "
                            f"(ETA T{f.eta_earliest_min:+g} + speed reduction {a.speed_reduction_min:g} "
                            f"+ ground delay {a.ground_delay_min:g})")
        sr, gd = a.speed_reduction_min, a.ground_delay_min
        if not (math.isfinite(sr) and 0 <= sr <= MAX_SPEED_REDUCTION_MIN):
            problems.append(f"speed reduction {sr:g} min outside 0-{MAX_SPEED_REDUCTION_MIN:g}")
        if not math.isfinite(gd):
            problems.append(f"non-finite ground delay {gd!r}")
        elif gd < 0:
            problems.append(f"negative ground delay {gd:g}")
        if math.isfinite(gd) and gd > 0 and f.airborne:
            problems.append(f"ground delay {gd:g} min impossible: {cs} already airborne")
        out.append(RuleCheck(
            "PLT-EARLIEST-ETA", "PILOT", not problems,
            f"{cs}: " + ("; ".join(problems) if problems else f"slot T{slot:+g} >= earliest achievable T{earliest:+g}"),
            SRC_OPS, {"slot_min": slot if slot_ok else "none", "eta_earliest_min": f.eta_earliest_min,
                      "speed_reduction_min": sr if math.isfinite(sr) else str(sr),
                      "ground_delay_min": gd if math.isfinite(gd) else str(gd),
                      "airborne": "yes" if f.airborne else "no"}, cs))

        # PLT-FUEL-RESERVE (unrounded hold)
        extra, alt, _, fin = fuel_info(f)
        hold = max(0.0, slot - earliest) if slot_ok else float("inf")
        ok = hold <= extra
        hold_s = f"{hold:.2f}".rstrip("0").rstrip(".") if slot_ok else "unknown"
        out.append(RuleCheck(
            "PLT-FUEL-RESERVE", "PILOT", ok,
            f"{cs}: hold {hold_s} min {'<=' if ok else '>'} {extra:g} min extra fuel above alternate "
            f"({alt or 'none'}) + {fin:g} min final reserve", SRC_FUEL,
            {"hold_min": round(hold, 2) if slot_ok else "unknown", "extra_min": extra,
             "alternate": str(alt or "none"), "final_reserve_min": fin}, cs))

        # GND-GATE-SIZE
        if g is None:
            out.append(RuleCheck("GND-GATE-SIZE", "GROUND", False, f"{cs}: unknown gate {a.gate!r}", SRC_OPS,
                                 {"gate": str(a.gate)}, cs))
        else:
            ok = g.remote or g.size == "wide" or f.gate_size == "narrow"
            out.append(RuleCheck(
                "GND-GATE-SIZE", "GROUND", ok,
                f"{cs}: gate {g.id} ({'remote stand' if g.remote else g.size + '-body'}) "
                f"{'fits' if ok else 'cannot take'} {f.aircraft_type or ''} ({f.gate_size}-body)",
                SRC_OPS, {"gate": g.id, "gate_size": g.size, "aircraft_size": f.gate_size}, cs))

        # GND-GATE-FREE
        out.append(gfree[cs])

        # GND-CREW-DUTY
        ok = m.overtime_min <= 0
        turn_end = (m.in_block_min or 0) + TURNAROUND_MIN
        out.append(RuleCheck(
            "GND-CREW-DUTY", "GROUND", ok,
            f"{cs}: turnaround ends T{turn_end:+g}, crew shift ends T{f.crew_shift_end_min:+g}"
            + ("" if ok else f" ({m.overtime_min:g} min overtime)"),
            SRC_OPS, {"turnaround_end_min": round(turn_end, 1), "crew_shift_end_min": f.crew_shift_end_min,
                      "overtime_min": m.overtime_min}, cs))
    return out


def feasible(wave: Wave, plan: JointPlan) -> bool:
    """True iff every rule passes. A structurally malformed plan is infeasible (never raises)."""
    try:
        return all(c.passed for c in check(wave, plan))
    except ValueError:
        return False


def violations(wave: Wave, plan: JointPlan) -> list[RuleCheck]:
    """Failing checks. Raises ValueError for a structurally malformed plan."""
    return [c for c in check(wave, plan) if not c.passed]
