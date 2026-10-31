import copy
from dataclasses import replace

import pytest

from atm.wave import rules
from atm.wave.model import Move, QueueArrival
from atm.wave.moves import _move, apply, fcfs_plan, legal_moves
from atm.wave.utility import evaluate
from tests.fixtures import make_wave


def test_fcfs_plan_shape():
    w = make_wave()
    p = fcfs_plan(w)
    assert sorted(a.callsign for a in p.assignments) == sorted(f.callsign for f in w.flights)
    for a in p.assignments:
        f = w.flight(a.callsign)
        assert a.action == "land" and a.slot_min >= f.eta_earliest_min
        assert a.gate == f.scheduled_gate and a.speed_reduction_min == 0 and a.ground_delay_min == 0
        assert isinstance(a.slot_min, float)
    assert all(c.passed for c in rules.check(w, p) if c.rule == "ATC-WAKE-SEP")


def test_pilot_cannot_move_other_flight():
    w = make_wave()
    p = fcfs_plan(w)
    ms = legal_moves(w, p, "PILOT:DL100")
    assert ms and all(m.callsigns == ["DL100"] and m.agent == "PILOT:DL100" for m in ms)
    assert {m.kind for m in ms} <= {"speed_reduction", "ground_delay", "request_divert", "accept"}
    assert any(m.kind == "accept" for m in ms)


def test_ground_delay_only_if_not_airborne():
    w = make_wave()
    p = fcfs_plan(w)
    air = legal_moves(w, p, "PILOT:DL100")      # airborne
    ground = legal_moves(w, p, "PILOT:UA403")   # not airborne
    assert not any(m.kind == "ground_delay" for m in air)
    assert any(m.kind == "speed_reduction" for m in air)
    assert any(m.kind == "ground_delay" for m in ground)
    # the engine also rejects a ground delay forced onto an airborne flight
    bad = p.with_assignment(replace(p.get("DL100"), ground_delay_min=5.0, slot_min=p.get("DL100").slot_min + 5))
    c = next(c for c in rules.check(w, bad) if c.rule == "PLT-EARLIEST-ETA" and c.callsign == "DL100")
    assert not c.passed and "airborne" in c.detail


def test_moves_within_authority_and_capped():
    w = make_wave()
    p = fcfs_plan(w)
    atc = legal_moves(w, p, "ATC")
    gnd = legal_moves(w, p, "GROUND")
    assert 0 < len(atc) <= 12 and 0 < len(gnd) <= 12
    assert {m.kind for m in atc} <= {"swap_order", "change_runway", "shift_slot", "speed_reduction", "ground_delay"}
    assert all(m.callsigns[0] != w.focus for m in atc if m.kind in ("speed_reduction", "ground_delay"))
    assert {m.kind for m in gnd} <= {"reassign_gate", "remote_stand", "swap_gates"}
    assert len({m.id for m in atc}) == len(atc)
    assert legal_moves(w, p, "ATC")[0].id == atc[0].id   # deterministic


def test_apply_is_pure():
    w = make_wave()
    p = fcfs_plan(w)
    evaluate(w, p)
    before = copy.deepcopy(p.to_dict())
    for ag in ("ATC", "GROUND", "PILOT:DL100", "PILOT:UA403"):
        for m in legal_moves(w, p, ag):
            p2 = apply(w, p, m)
            evaluate(w, p2)   # fills p2 metrics; must not leak into p
            assert p.to_dict() == before
            assert all(x is not y and x.metrics is not y.metrics
                       for x, y in zip(p.assignments, p2.assignments))


def test_apply_reslots_wake_legal():
    w = make_wave()
    p = fcfs_plan(w)
    for m in legal_moves(w, p, "ATC") + legal_moves(w, p, "PILOT:DL100") + legal_moves(w, p, "PILOT:UA403"):
        p2 = apply(w, p, m)
        wake = [c for c in rules.check(w, p2) if c.rule in ("ATC-WAKE-SEP", "PLT-EARLIEST-ETA")]
        assert all(c.passed for c in wake), (m.label, [c.detail for c in wake if not c.passed])


def test_swap_and_speed_reduction_effects():
    """Workbook wake times (Large->Large 60 s): FCFS DL100 T+5, DL201 T+7 (holds 1), AA302 T+8, UA403 T+12."""
    from atm.wave.moves import _move
    w = make_wave()
    p = fcfs_plan(w)
    sw = _move("ATC", "swap_order", ["DL201", "AA302"], {"runway": "26R"}, "swap")
    p2 = apply(w, p, sw)
    assert p2.get("AA302").slot_min < p2.get("DL201").slot_min
    assert evaluate(w, p) and p.get("DL201").metrics.hold_min == 1
    # DL201 absorbs 3 min en route: only DL201 moves (pilot authority), to its own earliest wake-legal slot
    sr = next(m for m in legal_moves(w, p, "PILOT:DL201") if m.kind == "speed_reduction" and m.params["minutes"] == 3)
    p5 = apply(w, p, sr)
    assert p5.get("DL201").slot_min >= 9.0 and p5.get("DL201").speed_reduction_min == 3
    assert all(p5.get(c).slot_min == p.get(c).slot_min for c in ("DL100", "AA302", "UA403"))
    assert all(c.passed for c in rules.check(w, p5) if c.rule == "ATC-WAKE-SEP")
    dv = next(m for m in legal_moves(w, p, "PILOT:AA302") if m.kind == "request_divert")
    p4 = apply(w, p, dv)
    assert p4.get("AA302").action == "divert" and evaluate(w, p4)["totals"]["diversions"] == 1


def test_pilot_move_does_not_move_others():
    w = make_wave()
    p = fcfs_plan(w)
    for cs in (f.callsign for f in w.flights):
        for m in legal_moves(w, p, f"PILOT:{cs}"):
            p2 = apply(w, p, m)
            changed = [a.callsign for a, b in zip(p.assignments, p2.assignments) if a.key() != b.key()]
            assert set(changed) <= {cs}, (m.id, changed)


def test_foreign_pilot_move_rejected():
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(ValueError, match="own flight"):
        apply(w, p, _move("PILOT:DL100", "speed_reduction", ["AA302"], {"minutes": 8.0}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("PILOT:DL100", "change_runway", ["DL100"], {"runway": "27L"}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("GROUND", "shift_slot", ["DL100"], {"minutes": 2.0}, "x"))
    with pytest.raises(ValueError, match="authority"):
        apply(w, p, _move("ATC", "reassign_gate", ["DL100"], {"gate": "B5"}, "x"))
    with pytest.raises(ValueError, match="unknown agent"):
        apply(w, p, Move("x", "TOWER", "accept", ["DL100"], {}, "x"))


def test_apply_validates_against_plan():
    w = make_wave()
    p = fcfs_plan(w)
    pd = apply(w, p, _move("PILOT:DL100", "request_divert", ["DL100"], {}, "x"))
    for m in (_move("ATC", "shift_slot", ["DL100"], {"minutes": 2.0}, "x"),
              _move("PILOT:DL100", "speed_reduction", ["DL100"], {"minutes": 8.0}, "x"),
              _move("GROUND", "remote_stand", ["DL100"], {"gate": "RS"}, "x")):
        with pytest.raises(ValueError, match="diverted"):
            apply(w, pd, m)
    stale = apply(w, p, _move("ATC", "change_runway", ["AA302"], {"runway": "27L"}, "x"))
    with pytest.raises(ValueError, match="same runway"):
        apply(w, stale, _move("ATC", "swap_order", ["DL201", "AA302"], {"runway": "26R"}, "x"))
    with pytest.raises(ValueError, match="not a flight"):
        apply(w, p, _move("ATC", "shift_slot", ["ZZ9"], {"minutes": 2.0}, "x"))


def test_unknown_agent_and_bad_gate_robust():
    w = make_wave()
    p = fcfs_plan(w)
    with pytest.raises(ValueError):
        legal_moves(w, p, "PILOT:ZZ9")
    with pytest.raises(ValueError):
        legal_moves(w, p, "TOWER")
    # 4c: a scheduled gate missing from the gate list must not crash Ground's move generation
    wb = make_wave(flights=[replace(w.flights[0], scheduled_gate="Z9")] + w.flights[1:])
    pb = fcfs_plan(wb)
    ms = legal_moves(wb, pb, "GROUND")
    assert ms and not any(m.kind == "swap_gates" and "DL100" in m.callsigns for m in ms)
    assert not rules.feasible(wb, pb)


def test_fcfs_follows_queue_direction_when_no_metar():
    w = make_wave(metar=None)    # queue lands 26R / 27L
    p = fcfs_plan(w)
    assert all(a.runway in ("26R", "27L") for a in p.assignments), p.describe()
    assert rules.feasible(w, p)
    calm = make_wave(metar=replace(make_wave().metar, wind_dir_deg=90, wind_kt=2))
    pc = fcfs_plan(calm)
    assert all(a.runway in ("26R", "27L") for a in pc.assignments), pc.describe()


def test_no_change_runway_against_flow():
    w = make_wave()
    p = fcfs_plan(w)
    for m in legal_moves(w, p, "ATC"):
        if m.kind == "change_runway":
            assert m.params["runway"] not in ("08L", "09R"), m.label
