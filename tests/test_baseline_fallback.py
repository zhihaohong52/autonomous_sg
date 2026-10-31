import time
from dataclasses import replace

from atm.wave import moves, rules
from atm.wave.baseline import status_quo
from atm.wave.fallback import solve
from atm.wave.model import Gate, agent_ids
from atm.wave.utility import agent_utility
from tests.fixtures import make_wave


def _storm():
    w = make_wave()
    m = replace(w.metar, wind_dir_deg=360, wind_kt=40, gust_kt=55, wx_codes=["TSRA"], surface="wet")
    return replace(w, metar=m)


def _gates_busy():
    w = make_wave()
    gates = [g if g.remote else replace(g, busy=[{"from_min": -100, "to_min": 500, "label": "X"}])
             for g in w.gates]
    return replace(w, gates=gates)


def _min_u(w, p):
    return min(agent_utility(w, p, a) for a in agent_ids(w))


def test_fallback_always_feasible():
    for w in (make_wave(), _storm(), _gates_busy()):
        plan, method = solve(w)
        assert rules.feasible(w, plan), method


def test_fallback_not_worse_than_fcfs():
    w = make_wave()
    f = moves.fcfs_plan(w)
    if rules.feasible(w, f):
        assert _min_u(w, solve(w)[0]) >= _min_u(w, f)


def test_fallback_deterministic():
    w = make_wave()
    assert solve(w)[0].id == solve(w)[0].id


def test_baseline_matches_fcfs_order():
    w = make_wave()
    plan = status_quo(w)
    by_slot = [a.callsign for a in sorted(plan.assignments, key=lambda a: a.slot_min)]
    by_eta = [f.callsign for f in sorted(w.flights, key=lambda f: (f.eta_earliest_min, f.callsign))]
    assert by_slot == by_eta
    assert plan.label.startswith("Status quo")


def test_fallback_runtime():
    w = make_wave()
    t = time.perf_counter()
    solve(w)
    assert time.perf_counter() - t < 2.0


def test_all_gates_busy_uses_remote_stand():
    w = _gates_busy()
    plan, _ = solve(w)
    assert rules.feasible(w, plan)
    # RS holds 2 aircraft: the rest go to the next escape (ground delay / divert), never past the cap
    on_rs = [a for a in plan.assignments if a.action == "land" and a.gate == "RS"]
    assert 1 <= len(on_rs) <= rules.REMOTE_STAND_POSITIONS


def test_bad_gate_never_raises():
    w = make_wave()
    for gid in (None, "ZZ"):
        w2 = replace(w, flights=[replace(f, scheduled_gate=gid) for f in w.flights])
        plan, method = solve(w2)
        assert rules.feasible(w2, plan), method
