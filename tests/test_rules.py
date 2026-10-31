from dataclasses import replace

import pytest

from atm.wave import rules
from atm.wave.model import Assignment, JointPlan, QueueArrival, agent_ids
from atm.wave.moves import fcfs_plan
from atm.wave.utility import evaluate
from tests.fixtures import make_wave


def _by(checks, rule, cs):
    return next(c for c in checks if c.rule == rule and c.callsign == cs)


def _plan(w, **over):
    p = fcfs_plan(w)
    for cs, kw in over.items():
        p = p.with_assignment(replace(p.get(cs), **kw))
    return p


def test_wake_violation_detected():
    w = make_wave(queue=[])
    # DL100 (HEAVY) at T+5, DL201 (LARGE) 1 min behind on the same runway: need 2.15 min
    p = _plan(w, DL100=dict(runway="27L", slot_min=5.0), DL201=dict(runway="27L", slot_min=6.0),
              AA302=dict(runway="26R", slot_min=8.0), UA403=dict(runway="26R", slot_min=12.0))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201")
    assert not c.passed
    assert c.evidence["leader"] == "DL100" and c.evidence["required_behind_min"] == 2.15  # Heavy->Large 5 nm = 129 s
    assert c.source.startswith("challenge2_operational_constraints.xlsx")
    assert _by(rules.check(w, p), "ATC-WAKE-SEP", "DL100").passed
    assert not rules.feasible(w, p)


def test_wake_violation_against_queue_follower():
    w = make_wave()   # F99 (queue) lands 26R at T+10
    p = _plan(w, AA302=dict(runway="26R", slot_min=9.5))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302")
    assert not c.passed and c.evidence["follower"] == "F99"


def test_wake_s_arrival_values():
    from atm.wave import constraints
    assert constraints.wake_s_arrival("Heavy", "Large") == 129     # 5 nm at 140 kt
    assert constraints.wake_s_arrival("Large", "Large") == 77      # other pair 3 nm
    assert constraints.wake_s_arrival("Super", "Small") == 206     # 8 nm
    assert constraints.wake_s_arrival("SUPER", "HEAVY") == 154     # model strings, 6 nm


def test_same_runway_pair_closer_than_arrival_spacing_fails():
    w = make_wave(queue=[])
    # DL201 and AA302 both LARGE on 27L: 3 nm = 77 s = 1.28 min. 1.2 min behind fails, 1.3 min passes
    base = dict(DL100=dict(runway="26R", slot_min=5.0), UA403=dict(runway="26R", slot_min=12.0),
                DL201=dict(runway="27L", slot_min=6.0))
    p = _plan(w, **base, AA302=dict(runway="27L", slot_min=7.2))
    c = _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302")
    assert not c.passed and c.evidence["required_behind_min"] == 1.28
    p = _plan(w, **base, AA302=dict(runway="27L", slot_min=7.3))
    assert _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302").passed


def test_wet_weather_factor():
    w = make_wave(queue=[])
    w = replace(w, metar=replace(w.metar, surface="wet"))
    # 2 min behind a HEAVY is legal dry (workbook 120 s) but needs 2.5 wet
    p = _plan(w, DL100=dict(runway="27L", slot_min=5.0), DL201=dict(runway="27L", slot_min=7.0))
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201").passed


def test_gate_overlap_detected():
    w = make_wave()
    p = fcfs_plan(w)
    p = p.with_assignment(replace(p.get("DL201"), gate="A2"))   # AA302 also at A2, minutes apart
    viol = rules.violations(w, p)
    gf = [c for c in viol if c.rule == "GND-GATE-FREE"]
    assert {c.callsign for c in gf} == {"DL201", "AA302"}
    # busy window from data (B2 occupied T+0..T+40 by DL55)
    p2 = fcfs_plan(w)
    p2 = p2.with_assignment(replace(p2.get("UA403"), gate="B2"))
    c = _by(rules.check(w, p2), "GND-GATE-FREE", "UA403")
    assert not c.passed and "DL55" in c.detail
    # remote stand has 2 positions: two overlapping aircraft are fine
    p3 = fcfs_plan(w)
    for cs in ("DL201", "AA302"):
        p3 = p3.with_assignment(replace(p3.get(cs), gate="RS"))
    assert all(_by(rules.check(w, p3), "GND-GATE-FREE", cs).passed for cs in ("DL201", "AA302"))


def test_remote_stand_two_positions():
    w = make_wave()
    # all four fixture flights are in block within one 45 min turnaround of each other
    p = fcfs_plan(w)
    for cs in ("DL100", "DL201", "AA302"):
        p = p.with_assignment(replace(p.get(cs), gate="RS"))
    c = _by(rules.check(w, p), "GND-GATE-FREE", "AA302")   # latest in-block of the three
    assert not c.passed and "full" in c.detail and "DL100" in c.detail and "DL201" in c.detail
    assert _by(rules.check(w, p), "GND-GATE-FREE", "DL100").passed
    assert _by(rules.check(w, p), "GND-GATE-FREE", "DL201").passed
    assert not rules.feasible(w, p)
    # two on RS: both pass
    p2 = fcfs_plan(w)
    for cs in ("DL100", "DL201"):
        p2 = p2.with_assignment(replace(p2.get(cs), gate="RS"))
    assert _by(rules.check(w, p2), "GND-GATE-FREE", "DL100").passed
    assert _by(rules.check(w, p2), "GND-GATE-FREE", "DL201").passed


def test_gate_size():
    w = make_wave()
    p = _plan(w, DL100=dict(gate="B1"))   # B772 wide-body into a narrow gate
    c = _by(rules.check(w, p), "GND-GATE-SIZE", "DL100")
    assert not c.passed


def test_wind_rejects_wet_15kt():
    w = make_wave()
    metar = replace(w.metar, raw="KATL 151551Z 18418KT 3SM RA BKN010 24/22 A2992", wind_dir_deg=184,
                    wind_kt=18, gust_kt=None, visibility_sm=3, wx_codes=["RA"], surface="wet")
    w = replace(w, metar=metar)
    p = _plan(w, DL201=dict(runway="26R"))
    c = _by(rules.check(w, p), "ATC-WIND-LIMITS", "DL201")
    assert not c.passed
    assert c.evidence["metar"] == metar.raw
    assert c.evidence["limit_crosswind_kt"] == 15
    assert c.evidence["crosswind_kt"] > 15
    assert "8400.9" in c.source


def test_all_runways_out_of_limits():
    w = make_wave()
    metar = replace(w.metar, raw="KATL 151551Z 36040G55KT 2SM +TSRA OVC008 22/21 A2970", wind_dir_deg=360,
                    wind_kt=40, gust_kt=55, visibility_sm=2, wx_codes=["+TSRA"], surface="wet")
    w = replace(w, metar=metar)
    for r in w.airport.runways:
        p = JointPlan([Assignment(f.callsign, "land", r.id, f.eta_earliest_min + 4 * i, f.scheduled_gate)
                       for i, f in enumerate(w.flights)])
        checks = rules.check(w, p)
        wl = [c for c in checks if c.rule == "ATC-WIND-LIMITS"]
        assert len(wl) == len(w.flights) and not any(c.passed for c in wl)
        assert not rules.feasible(w, p)
    ev = evaluate(w, fcfs_plan(w))   # must not crash
    assert ev["feasible"] is False


def test_fuel_and_eta_rules():
    w = make_wave()
    p = _plan(w, AA302=dict(slot_min=30.0))   # 22 min hold vs 16 min extra fuel
    c = _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302")
    assert not c.passed and c.evidence["extra_min"] == 16 and "121.639" in c.source
    p = _plan(w, DL201=dict(slot_min=3.0))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL201").passed
    # speed reduction can't be claimed without the slot moving
    p = _plan(w, DL100=dict(speed_reduction_min=8.0))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL100").passed


def test_divert_skips_landing_rules():
    w = make_wave()
    p = _plan(w, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
    cs = [c for c in rules.check(w, p) if c.callsign == "DL100"]
    assert [c.rule for c in cs] == ["PLT-FUEL-RESERVE"] and cs[0].passed


def test_evaluate_shape():
    w = make_wave()
    p = fcfs_plan(w)
    ev = evaluate(w, p)
    assert set(ev) == {"plan_id", "feasible", "checks", "utilities", "totals"}
    assert ev["plan_id"] == p.id
    assert set(ev["totals"]) == {"delay_min", "fuel_kg", "hold_min", "pax_protected_pct", "overtime_min", "diversions"}
    from atm.wave.utility import negotiators
    assert set(ev["utilities"]) == set(negotiators(w)) and len(ev["utilities"]) == 3
    assert {"ATC", "GROUND"} <= set(ev["utilities"]) and all(0 <= u <= 1 for u in ev["utilities"].values())
    keys = {"rule", "owner", "passed", "detail", "source", "evidence", "callsign"}
    assert all(set(c) == keys for c in ev["checks"])
    for c in ev["checks"]:
        assert all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in c["evidence"].values()), c
    rules_seen = {c["rule"] for c in ev["checks"]}
    assert rules_seen == {"ATC-RWY-OPEN", "ATC-WAKE-SEP", "ATC-TAXI-ROUTE", "ATC-WIND-LIMITS", "PLT-EARLIEST-ETA",
                          "PLT-FUEL-RESERVE", "GND-GATE-SIZE", "GND-GATE-FREE", "GND-CREW-DUTY",
                          "GND-GATE-NOTICE", "ATC-RWY-CAPACITY"}
    assert all(a.metrics.in_block_min is not None for a in p.assignments)


def test_closed_runway():
    w = make_wave()
    rw = [replace(r, open=False, closed_reason="NOTAM") if r.id == "26R" else r for r in w.airport.runways]
    w = replace(w, airport=replace(w.airport, runways=rw))
    p = _plan(w, DL100=dict(runway="26R"))
    c = _by(rules.check(w, p), "ATC-RWY-OPEN", "DL100")
    assert not c.passed and "NOTAM" in c.detail
    assert all(a.runway != "26R" for a in fcfs_plan(w).assignments)


def test_strip_of():
    assert rules.strip_of("08L") == ("08L", "26R") == rules.strip_of("26R")
    assert rules.strip_of("09C") == ("09C", "27C")
    assert rules.strip_of("36") == ("18", "36")
    assert rules.strip_of("01R") == ("01R", "19L")


def test_opposite_ends_rejected():
    # review case: no METAR, wave lands 08L while the queue lands 26R on the same strip
    w = make_wave(metar=None)
    p = JointPlan([Assignment("DL100", "land", "08L", 5.0, "A5"), Assignment("DL201", "land", "08L", 8.0, "A1"),
                   Assignment("AA302", "land", "08L", 10.0, "A2"), Assignment("UA403", "land", "08L", 22.0, "B1")])
    viol = rules.violations(w, p)
    rw = [c for c in viol if c.rule == "ATC-RWY-OPEN"]
    assert {c.callsign for c in rw} == {"DL100", "DL201", "AA302", "UA403"}
    assert "opposite-direction use of strip 08L/26R (queue landing" in rw[0].detail
    # wake across ends of the same strip: AA302 at 08L T+10 is simultaneous with queue F99 26R T+10
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "AA302").passed
    # SUPER cross-end case: queue SUPER on 27L, wave flight 2 min later on 09R
    q = [QueueArrival("Q1", "A388", "SUPER", "27L", 5.0)]
    w2 = make_wave(queue=q, metar=None)
    p2 = _plan(w2, DL100=dict(runway="09R", slot_min=7.0), DL201=dict(runway="26R", slot_min=6.0),
               AA302=dict(runway="26R", slot_min=8.0), UA403=dict(runway="26R", slot_min=12.0))
    cs = rules.check(w2, p2)
    assert not _by(cs, "ATC-RWY-OPEN", "DL100").passed
    wk = _by(cs, "ATC-WAKE-SEP", "DL100")
    assert not wk.passed and wk.evidence["leader"] == "Q1" and wk.evidence["required_behind_min"] == 2.57  # Super->Heavy 6 nm = 154 s


def test_divert_needs_alternate():
    w = make_wave()
    for fuel in ({"extra_min": 30}, {"extra_min": 30, "alternate": None, "alternate_min": None,
                                     "final_reserve_min": None},
                 {"extra_min": 30, "alternate": "BHM", "alternate_min": 0}):
        wf = make_wave(flights=[replace(w.flights[0], fuel=fuel)] + w.flights[1:])
        p = _plan(wf, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
        c = _by(rules.check(wf, p), "PLT-FUEL-RESERVE", "DL100")
        assert not c.passed and "121.639" in c.source, fuel
        assert rules.feasible(wf, fcfs_plan(wf)) in (True, False)   # no crash with None fuel values
        evaluate(wf, p)


def test_plan_structure_enforced():
    w = make_wave()
    p = fcfs_plan(w)
    bad = [JointPlan(p.assignments[:1]), JointPlan([]),
           JointPlan(p.assignments + [replace(p.get("DL100"), runway="09R", gate="RS")]),
           JointPlan(p.assignments + [Assignment("XX1", "land", "26R", 30.0, "RS")])]
    for b in bad:
        with pytest.raises(ValueError, match="must match wave flights"):
            rules.check(w, b)
        assert rules.feasible(w, b) is False
        ev = evaluate(w, b)
        assert ev["feasible"] is False and ev["checks"] == []
        assert set(ev) == {"plan_id", "feasible", "checks", "utilities", "totals"}


def test_fuel_uses_unrounded_hold():
    w = make_wave()   # AA302: ETA T+8, extra fuel 16 min
    p = _plan(w, AA302=dict(slot_min=24.04))
    assert not _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302").passed
    p = _plan(w, AA302=dict(slot_min=24.0))
    assert _by(rules.check(w, p), "PLT-FUEL-RESERVE", "AA302").passed


def test_nan_slot_rejected_cleanly():
    import json
    w = make_wave()
    p = _plan(w, DL201=dict(slot_min=float("nan")))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL201").passed
    assert not _by(rules.check(w, p), "ATC-WAKE-SEP", "DL201").passed
    json.dumps(evaluate(w, p), allow_nan=False)


def test_nan_ground_delay_rejected():
    import json
    w = make_wave()
    p = _plan(w, DL100=dict(ground_delay_min=float("nan")))
    assert not _by(rules.check(w, p), "PLT-EARLIEST-ETA", "DL100").passed
    assert not rules.feasible(w, p)
    json.dumps(evaluate(w, p), allow_nan=False)


def test_nonfinite_minutes_rejected():
    from atm.wave.model import Move
    from atm.wave.moves import apply as apply_move
    w = make_wave()
    p = fcfs_plan(w)
    for m in (float("inf"), float("nan")):
        mv = Move(id="x", agent="PILOT:UA403", kind="ground_delay", callsigns=["UA403"],
                  params={"minutes": m}, label="x")
        with pytest.raises(ValueError, match="minutes must be a finite number"):
            apply_move(w, p, mv)
    mv = Move(id="x", agent="ATC", kind="shift_slot", callsigns=["DL201"],
              params={"minutes": float("nan")}, label="x")
    with pytest.raises(ValueError, match="minutes must be a finite number"):
        apply_move(w, p, mv)


def test_assignment_dict_omits_none():
    w = make_wave()
    p = _plan(w, DL100=dict(action="divert", runway=None, slot_min=None, gate=None))
    d = p.to_dict()["assignments"][0]
    assert d["action"] == "divert" and not {"runway", "slot_min", "gate"} & set(d)
    assert {"runway", "slot_min", "gate"} <= set(p.to_dict()["assignments"][1])


def test_divert_delay_includes_existing_delay():
    """A diverting flight keeps the delay it already has (late inbound), plus alternate + 90."""
    from dataclasses import replace as _r
    from atm.wave.model import Assignment, FlightMetrics
    from atm.wave.utility import DIVERT_EXTRA_DELAY_MIN, metrics
    from tests.fixtures import make_wave
    w = make_wave()
    f = w.flights[0]
    late = _r(f, eta_earliest_min=f.sched_min + 200)
    w2 = _r(w, flights=[late] + list(w.flights[1:]))
    m = metrics(w2, Assignment(f.callsign, "divert", None, None, None, 0.0, 0.0, FlightMetrics()))
    assert m.delay_min == pytest.approx(200 + float(f.fuel["alternate_min"]) + DIVERT_EXTRA_DELAY_MIN)


def test_limits_entries_present():
    from atm.wave import rules
    names = {l["limit"]: l for l in rules.LIMITS}
    assert names["remote stand"]["value"] == "1 stand, 2 positions"
    assert "speed reduction cap" in names
    assert "departures not modelled" in names["runway capacity"]["note"]
