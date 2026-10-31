from dataclasses import replace

from atm.wave import rules
from atm.wave.model import Assignment
from atm.wave.moves import fcfs_plan
from tests.fixtures import make_wave


def _with_duty(w, cs, end):
    return replace(w, flights=[replace(f, crew_duty_end_min=end) if f.callsign == cs else f for f in w.flights])


def _duty(w, p, cs):
    return next((c for c in rules.check(w, p) if c.rule == "PLT-CREW-DUTY" and c.callsign == cs), None)


def test_skipped_when_unknown():
    w = make_wave()
    assert _duty(w, fcfs_plan(w), "DL201") is None
    assert "crew_duty_end_min" not in w.to_dict()["flights"][0]


def test_plan_caused_bust_fails_and_serializes():
    w = _with_duty(make_wave(), "DL201", 30.0)   # eta 6 + taxi fits in T+30
    p = fcfs_plan(w)
    assert _duty(w, p, "DL201").passed
    late = p.with_assignment(replace(p.get("DL201"), slot_min=40.0))
    c = _duty(w, late, "DL201")
    assert not c.passed and c.owner == "PILOT" and c.evidence["unavoidable_bust"] == "no"
    assert w.to_dict()["flights"][1]["crew_duty_end_min"] == 30.0


def test_unavoidable_bust_is_reported_not_a_violation():
    w = _with_duty(make_wave(), "DL201", 0.0)    # even eta + taxi is past the duty end
    p = fcfs_plan(w)
    late = p.with_assignment(replace(p.get("DL201"), slot_min=p.get("DL201").slot_min + 20))
    for plan in (p, late, p.with_assignment(Assignment("DL201", "divert"))):
        c = _duty(w, plan, "DL201")
        assert c.passed and c.evidence["unavoidable_bust"] == "yes" and "unavoidable duty bust (data)" in c.detail


def test_divert_fails_when_landing_would_have_fitted():
    w = _with_duty(make_wave(), "DL201", 20.0)   # eta 6 + taxi fits; alternate arrival 6 + 35 = 41 does not
    c = _duty(w, fcfs_plan(w).with_assignment(Assignment("DL201", "divert")), "DL201")
    assert not c.passed
    w2 = _with_duty(make_wave(), "DL201", 100.0)
    assert _duty(w2, fcfs_plan(w2).with_assignment(Assignment("DL201", "divert")), "DL201").passed
