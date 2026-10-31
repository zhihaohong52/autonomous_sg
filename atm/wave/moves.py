"""FCFS opening plan, legal moves per agent (spec §6 authority) and pure move application.

Authority is enforced here, legality in rules.py:
  * ATC changes runway / slot / landing order (and may re-slot other flights on the strip); for
    flights without a pilot agent (every flight except wave.focus) it also issues speed control
    (airborne) or a ground delay (not yet airborne) as ATC instructions.
  * PILOT:<cs> changes only its own flight (speed reduction, ground delay, divert, accept); the
    flight is placed at its own earliest wake-legal slot with every other slot fixed.
  * GROUND changes gates only.
"""
from __future__ import annotations

import math
from dataclasses import replace

from atm.wave import rules, wind
from atm.wave.model import Assignment, FlightMetrics, JointPlan, Move, Wave
from atm.wave.rules import has_slot, reciprocal, strip_of, strip_sequence, wake_gap_min, weather_factor
from atm.wave.utility import agent_utility, metrics, turnaround_min

MAX_MOVES = 12
SPEED_REDUCTIONS = (3.0, 5.0, 8.0)
GROUND_DELAYS = (5.0, 10.0, 15.0)
SHIFT_STEPS = (-2.0, 2.0)
SLOT_HORIZON_MIN = 180.0       # ATC re-slot search horizon
PILOT_SEARCH_MIN = 60.0        # a Pilot's own-slot search horizon
CALM_KT = 3.0                  # at or below: wind gives no runway preference, follow the queue

AUTHORITY = {
    "ATC": {"swap_order", "change_runway", "shift_slot", "speed_reduction", "ground_delay"},
    "PILOT": {"speed_reduction", "ground_delay", "request_divert", "accept"},
    "GROUND": {"reassign_gate", "remote_stand", "swap_gates"},
}


def _r(x: float) -> float:
    """Normalise slot values up to 0.1 min (JointPlan.id hashes repr, so 5 vs 5.0 must not
    differ; ceiling so a rounded slot never lands inside a separation or before an ETA)."""
    return float(math.ceil(round(x * 10, 6)) / 10)


def _fresh(a: Assignment, **kw) -> Assignment:
    return replace(a, metrics=FlightMetrics(), **kw)


def _copy(plan: JointPlan) -> JointPlan:
    return JointPlan([_fresh(a) for a in plan.assignments], plan.label)


def _flight(wave: Wave, cs: str):
    return next((f for f in wave.flights if f.callsign == cs), None)


def _asg(plan: JointPlan, cs: str) -> Assignment | None:
    return next((a for a in plan.assignments if a.callsign == cs), None)


# ---------------------------------------------------------------- slotting

def _earliest_legal(wave: Wave, others: list[tuple[float, str]], wake: str, floor: float) -> float:
    """Earliest t >= floor wake-legal against every (slot, wake) on the strip, both directions.
    Returns floor + SLOT_HORIZON_MIN if nothing is found (engine will judge it)."""
    fac = weather_factor(wave)
    cands = sorted({_r(floor)} | {_r(s + wake_gap_min(w, wake) * fac) for s, w in others
                                  if s + wake_gap_min(w, wake) * fac > floor})
    for t in cands:
        if all((t >= s and t - s >= round(wake_gap_min(w, wake) * fac, 2) - 1e-9) or
               (t < s and s - t >= round(wake_gap_min(wake, w) * fac, 2) - 1e-9) for s, w in others):
            return t
    return _r(floor + SLOT_HORIZON_MIN)


def _min_slot(wave: Wave, a: Assignment) -> float:
    f = _flight(wave, a.callsign)
    return f.eta_earliest_min + a.speed_reduction_min + a.ground_delay_min


def _on_strip(plan: JointPlan, runway: str) -> list[Assignment]:
    key = strip_of(runway)
    return [a for a in plan.assignments if a.action == "land" and a.runway and strip_of(a.runway) == key]


def _reslot(wave: Wave, plan: JointPlan, runway: str, floors: dict[str, float],
            order: list[str] | None = None) -> JointPlan:
    """ATC re-slot of every wave flight on the strip of `runway` (both ends): in `order` (default:
    by floor), each to the earliest wake-legal slot >= max(its floor, earliest achievable, previous
    wave flight). Flights without an explicit floor keep their current slot as floor (never pulled
    earlier)."""
    on = _on_strip(plan, runway)
    fl = {a.callsign: max(floors.get(a.callsign, a.slot_min if has_slot(a) else -1e9), _min_slot(wave, a))
          for a in on}
    if order is None:
        order = sorted(fl, key=lambda c: (fl[c], c))
    key = strip_of(runway)
    placed = [(float(q.slot_min), q.wake) for q in wave.queue if strip_of(q.runway) == key]
    prev = -1e9
    new = {}
    by_cs = {a.callsign: a for a in on}
    for cs in order:
        wk = _flight(wave, cs).wake
        t = _earliest_legal(wave, placed, wk, max(fl[cs], prev))
        placed.append((t, wk))
        prev = t
        new[cs] = _fresh(by_cs[cs], slot_min=t)
    return JointPlan([new.get(a.callsign) or _fresh(a) for a in plan.assignments], plan.label)


def _place_own(wave: Wave, plan: JointPlan, cs: str, floor: float) -> JointPlan:
    """Pilot authority: move only `cs` to its earliest wake-legal slot >= floor, every other slot
    fixed. If none within PILOT_SEARCH_MIN, place it at the floor and let the engine reject it."""
    a = plan.get(cs)
    others = [(x[0], x[2]) for x in strip_sequence(wave, plan, a.runway) if x[1] != cs]
    t = _earliest_legal(wave, others, _flight(wave, cs).wake, floor)
    if t > floor + PILOT_SEARCH_MIN:
        t = _r(floor)
    return plan.with_assignment(_fresh(a, slot_min=t))


def _ends_in_use(wave: Wave, plan: JointPlan | None, exclude: str | None = None) -> dict[str, int]:
    """Runway end -> number of landings using it (queue + wave flights except `exclude`)."""
    n: dict[str, int] = {}
    for q in wave.queue:
        n[q.runway] = n.get(q.runway, 0) + 1
    for a in (plan.assignments if plan else []):
        if a.action == "land" and a.runway and a.callsign != exclude:
            n[a.runway] = n.get(a.runway, 0) + 1
    return n


def _wind_rank(wave: Wave, aircraft_type: str | None, in_use: dict[str, int] | None = None) -> list[str]:
    """Open runways, best first: within wind limits; not against the flow already in use on the
    strip; (calm wind / no METAR) most used by the queue; least crosswind+tailwind; most headwind."""
    in_use = in_use or {}
    q_use: dict[str, int] = {}
    for q in wave.queue:
        q_use[q.runway] = q_use.get(q.runway, 0) + 1
    m = wave.metar
    calm = m is None or (m.wind_kt <= CALM_KT and (m.gust_kt or 0) <= CALM_KT)
    rows = []
    for i, r in enumerate(wave.airport.runways):
        if not r.open:
            continue
        against = 1 if in_use.get(reciprocal(r.id) or "", 0) > 0 else 0
        follow = -q_use.get(r.id, 0) if calm else 0
        if m is None:
            rows.append(((0, against, follow, 0.0, 0.0, i), r.id))
            continue
        hw, xw, tw = wind.components(m.wind_dir_deg, m.wind_kt, m.gust_kt, r.heading_true)
        lxw, ltw, _ = wind.limits(m.surface, aircraft_type)
        ok = wind.within_limits(xw, tw, lxw, ltw)
        rows.append(((0 if ok else 1, against, follow, xw + tw, -hw, i), r.id))
    return [rid for _, rid in sorted(rows)]


def fcfs_plan(wave: Wave) -> JointPlan:
    """First come first served: by earliest ETA, best-ranked open runway (see _wind_rank), earliest
    wake-legal slot behind the queue and already placed flights, scheduled gate, no absorption."""
    placed: dict[tuple, list[tuple[float, str]]] = {}
    in_use: dict[str, int] = {}
    for q in wave.queue:
        placed.setdefault(strip_of(q.runway), []).append((float(q.slot_min), q.wake))
        in_use[q.runway] = in_use.get(q.runway, 0) + 1
    out = {}
    for f in sorted(wave.flights, key=lambda f: (f.eta_earliest_min, f.callsign)):
        ranked = _wind_rank(wave, f.aircraft_type, in_use)
        rw = ranked[0] if ranked else (wave.airport.runways[0].id if wave.airport.runways else None)
        key = strip_of(rw) if rw else ("",)
        t = _earliest_legal(wave, placed.get(key, []), f.wake, f.eta_earliest_min)
        placed.setdefault(key, []).append((t, f.wake))
        if rw:
            in_use[rw] = in_use.get(rw, 0) + 1
        out[f.callsign] = Assignment(f.callsign, "land", rw, t, f.scheduled_gate)
    return JointPlan([out[f.callsign] for f in wave.flights], "FCFS")


# ---------------------------------------------------------------- moves

def _pstr(params: dict) -> str:
    return ",".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in sorted(params.items()))


def _move(agent: str, kind: str, callsigns: list[str], params: dict, label: str) -> Move:
    return Move(f"{agent}:{kind}:{'-'.join(callsigns)}:{_pstr(params)}", agent, kind, callsigns, params, label)


def _check_agent(wave: Wave, plan: JointPlan, agent_id: str) -> str:
    """Return the agent role (ATC|GROUND|PILOT); ValueError for an unknown agent id."""
    if agent_id in ("ATC", "GROUND"):
        return agent_id
    if isinstance(agent_id, str) and agent_id.startswith("PILOT:"):
        cs = agent_id.split(":", 1)[1]
        if _flight(wave, cs) is not None and _asg(plan, cs) is not None:
            return "PILOT"
    raise ValueError(f"unknown agent {agent_id!r}")


def _candidates(wave: Wave, plan: JointPlan, agent_id: str) -> list[Move]:
    role = _check_agent(wave, plan, agent_id)
    mv: list[Move] = []
    landing = [a for a in plan.assignments if a.action == "land"]
    if role == "ATC":
        open_rw = [r.id for r in wave.airport.runways if r.open]
        seen = set()
        for a in landing:
            if not a.runway or strip_of(a.runway) in seen:
                continue
            seen.add(strip_of(a.runway))
            seq = [x for x in strip_sequence(wave, plan, a.runway) if x[3]]
            for (_, c1, _, _, e1), (_, c2, _, _, e2) in zip(seq, seq[1:]):
                if e1 == e2:
                    mv.append(_move("ATC", "swap_order", [c1, c2], {"runway": e1},
                                    f"Swap landing order {c1} ↔ {c2} on {e1}"))
        for a in landing:
            in_use = _ends_in_use(wave, plan, exclude=a.callsign)
            for rw in open_rw:
                if rw == a.runway or in_use.get(reciprocal(rw) or "", 0) > 0:
                    continue   # never offer landing against the flow already in use on that strip
                mv.append(_move("ATC", "change_runway", [a.callsign], {"runway": rw},
                                f"Move {a.callsign} from {a.runway} to runway {rw}"))
            if a.runway and has_slot(a):
                for d in SHIFT_STEPS:
                    mv.append(_move("ATC", "shift_slot", [a.callsign], {"minutes": d},
                                    f"Shift {a.callsign} slot {d:+g} min"))
            if a.callsign != getattr(wave, "focus", None) and a.runway and has_slot(a):
                # one ATC absorption instruction per holding non-focus flight: the smallest step that
                # covers its airborne hold (speed control if airborne, ground delay if not)
                f = _flight(wave, a.callsign)
                hold = metrics(wave, a).hold_min
                if hold > 0:
                    steps = SPEED_REDUCTIONS if f.airborne else GROUND_DELAYS
                    v = next((x for x in steps if x >= hold), steps[-1])
                    cur = a.speed_reduction_min if f.airborne else a.ground_delay_min
                    if v > cur:
                        if f.airborne:
                            mv.append(_move("ATC", "speed_reduction", [a.callsign], {"minutes": v},
                                            f"ATC speed control: {a.callsign} absorbs {v:g} min en route"))
                        else:
                            mv.append(_move("ATC", "ground_delay", [a.callsign], {"minutes": v},
                                            f"ATC ground delay: {a.callsign} holds {v:g} min at {f.origin}"))
    elif role == "GROUND":
        gates = {g.id: g for g in wave.gates}
        mm = {a.callsign: metrics(wave, a) for a in landing}
        turn = turnaround_min(wave)
        for a in landing:
            f = _flight(wave, a.callsign)
            ib = mm[a.callsign].in_block_min or 0.0
            end = ib + turn
            for g in wave.gates:
                if g.remote or g.id == a.gate or (f.gate_size == "wide" and g.size != "wide"):
                    continue
                if any(b["from_min"] < end and ib < b["to_min"] for b in g.busy):
                    continue
                if any(o.gate == g.id and o is not a and (mm[o.callsign].in_block_min or 0) < end
                       and ib < (mm[o.callsign].in_block_min or 0) + turn for o in landing):
                    continue
                mv.append(_move("GROUND", "reassign_gate", [a.callsign], {"gate": g.id},
                                f"Reassign {a.callsign} from gate {a.gate} to {g.id}"))
            rs = next((g for g in wave.gates if g.remote), None)
            if rs is not None and a.gate != rs.id:
                mv.append(_move("GROUND", "remote_stand", [a.callsign], {"gate": rs.id},
                                f"Send {a.callsign} to remote stand {rs.id} (bussing)"))
        for i, a in enumerate(landing):
            for b in landing[i + 1:]:
                ga, gb = gates.get(a.gate or ""), gates.get(b.gate or "")
                if ga is None or gb is None or ga.id == gb.id:
                    continue   # unknown gate: nothing to swap
                fa, fb = _flight(wave, a.callsign), _flight(wave, b.callsign)
                if (fa.gate_size == "wide" and gb.size != "wide" and not gb.remote) or \
                   (fb.gate_size == "wide" and ga.size != "wide" and not ga.remote):
                    continue
                mv.append(_move("GROUND", "swap_gates", [a.callsign, b.callsign], {},
                                f"Swap gates {a.callsign} ({a.gate}) ↔ {b.callsign} ({b.gate})"))
    else:
        cs = agent_id.split(":", 1)[1]
        a = plan.get(cs)
        f = _flight(wave, cs)
        if a.action == "land" and a.runway and has_slot(a):
            if f.airborne:
                for v in SPEED_REDUCTIONS:
                    if v != a.speed_reduction_min:
                        mv.append(_move(agent_id, "speed_reduction", [cs], {"minutes": v},
                                        f"{cs} absorbs {v:g} min by speed reduction en route"))
            else:
                for v in GROUND_DELAYS:
                    if v != a.ground_delay_min:
                        mv.append(_move(agent_id, "ground_delay", [cs], {"minutes": v},
                                        f"{cs} takes {v:g} min ground delay at {f.origin}"))
        if a.action == "land":
            alt = (f.fuel or {}).get("alternate") or "alternate"
            mv.append(_move(agent_id, "request_divert", [cs], {"alternate": str(alt)},
                            f"{cs} requests diversion to {alt}"))
        mv.append(_move(agent_id, "accept", [cs], {}, f"{cs} accepts current plan"))
    return mv


def legal_moves(wave: Wave, plan: JointPlan, agent_id: str) -> list[Move]:
    """Moves within the agent's authority, most useful first (fewest violations after the move,
    then the agent's own utility), at most MAX_MOVES. Infeasible moves are kept (ranked lower) so the
    engine can visibly reject them. No-ops are dropped except a Pilot's `accept`.
    Raises ValueError for an unknown agent id."""
    pid = plan.id
    scored = []
    for m in _candidates(wave, plan, agent_id):
        p2 = apply(wave, plan, m)
        if m.kind != "accept" and p2.id == pid:
            continue
        nv = sum(1 for c in rules.check(wave, p2) if not c.passed)
        scored.append(((nv, -agent_utility(wave, p2, agent_id), m.id), m))
    scored.sort(key=lambda x: x[0])
    out = [m for _, m in scored[:MAX_MOVES]]
    acc = next((m for _, m in scored if m.kind == "accept"), None)
    if acc is not None and acc not in out:
        out[-1] = acc
    return out


def _validate(wave: Wave, plan: JointPlan, move: Move) -> None:
    """Authority + applicability checks. ValueError with a clear message on any mismatch."""
    k, cs, agent = move.kind, list(move.callsigns or []), move.agent
    if agent in ("ATC", "GROUND"):
        role = agent
    elif isinstance(agent, str) and agent.startswith("PILOT:"):
        role = "PILOT"
        own = agent.split(":", 1)[1]
        if cs != [own]:
            raise ValueError(f"{agent} may only move its own flight {own}, not {cs}")
    else:
        raise ValueError(f"unknown agent {agent!r}")
    if k not in AUTHORITY[role]:
        raise ValueError(f"{agent} has no authority for move kind {k!r} (allowed: {sorted(AUTHORITY[role])})")
    if role == "ATC" and k in ("speed_reduction", "ground_delay") and cs and cs[0] == getattr(wave, "focus", None):
        raise ValueError(f"ATC may not set {k} for {cs[0]}: that flight's pilot agent owns it")
    need = 2 if k in ("swap_order", "swap_gates") else 1
    if len(cs) != need or len(set(cs)) != need:
        raise ValueError(f"{k} needs {need} distinct callsign(s), got {cs}")
    for c in cs:
        if _asg(plan, c) is None or _flight(wave, c) is None:
            raise ValueError(f"{c} is not a flight of this plan/wave")
    if k == "accept":
        return
    asg = [plan.get(c) for c in cs]
    for a in asg:
        if a.action != "land":
            raise ValueError(f"{k} not applicable: {a.callsign} is diverted")
        if k in ("swap_order", "change_runway", "shift_slot", "speed_reduction", "ground_delay") \
                and (not a.runway or not has_slot(a)):
            raise ValueError(f"{k} not applicable: {a.callsign} has no runway/slot")
    if k == "swap_order" and asg[0].runway != asg[1].runway:
        raise ValueError(f"swap_order needs both flights on the same runway: "
                         f"{cs[0]} on {asg[0].runway}, {cs[1]} on {asg[1].runway}")
    if k == "change_runway" and not move.params.get("runway"):
        raise ValueError("change_runway needs params.runway")
    if k in ("reassign_gate", "remote_stand") and not move.params.get("gate"):
        raise ValueError(f"{k} needs params.gate")
    if k in ("shift_slot", "speed_reduction", "ground_delay"):
        try:
            minutes = float(move.params["minutes"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{k} needs numeric params.minutes") from None
        if not math.isfinite(minutes):
            raise ValueError("minutes must be a finite number")


def apply(wave: Wave, plan: JointPlan, move: Move) -> JointPlan:
    """Return a new plan with `move` applied (input never mutated). Raises ValueError if the move
    is outside the agent's authority or does not apply to the current plan."""
    _validate(wave, plan, move)
    k, cs, p = move.kind, move.callsigns, move.params
    new = _copy(plan)
    if k == "accept":
        return new
    if k == "swap_order":
        c1, c2 = cs
        a1, a2 = new.get(c1), new.get(c2)
        seq = [x[1] for x in strip_sequence(wave, new, a1.runway) if x[3]]
        i, j = seq.index(c1), seq.index(c2)
        seq[i], seq[j] = seq[j], seq[i]
        lo = min(a1.slot_min, a2.slot_min)
        floors = {c: (lo if c in (c1, c2) else new.get(c).slot_min) for c in seq}
        return _reslot(wave, new, a1.runway, floors, order=seq)
    if k == "change_runway":
        a = new.get(cs[0])
        new = new.with_assignment(_fresh(a, runway=p["runway"]))
        return _reslot(wave, new, p["runway"], {a.callsign: _min_slot(wave, a)})
    if k == "shift_slot":
        a = new.get(cs[0])
        return _reslot(wave, new, a.runway, {a.callsign: max(_min_slot(wave, a), a.slot_min + float(p["minutes"]))})
    if k in ("speed_reduction", "ground_delay"):
        a = new.get(cs[0])
        field = "speed_reduction_min" if k == "speed_reduction" else "ground_delay_min"
        a2 = _fresh(a, **{field: float(p["minutes"])})
        new = new.with_assignment(a2)
        return _place_own(wave, new, a2.callsign, max(a2.slot_min, _min_slot(wave, a2)))
    if k == "request_divert":
        a = new.get(cs[0])
        return new.with_assignment(_fresh(a, action="divert", runway=None, slot_min=None, gate=None,
                                          speed_reduction_min=0.0, ground_delay_min=0.0))
    if k in ("reassign_gate", "remote_stand"):
        a = new.get(cs[0])
        return new.with_assignment(_fresh(a, gate=p["gate"]))
    if k == "swap_gates":
        a, b = new.get(cs[0]), new.get(cs[1])
        new = new.with_assignment(_fresh(a, gate=b.gate))
        return new.with_assignment(_fresh(new.get(b.callsign), gate=a.gate))
    raise ValueError(f"unknown move kind {k!r}")
