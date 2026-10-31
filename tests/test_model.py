from atm.wave.model import Assignment, JointPlan
from tests.fixtures import make_wave


def test_plan_id_stable_and_roundtrip():
    a = Assignment("DL100", runway="26R", slot_min=6, gate="A5")
    b = Assignment("DL201", runway="27L", slot_min=7, gate="A1")
    assert JointPlan([a, b]).id == JointPlan([b, a]).id
    assert JointPlan([a, b]).id != JointPlan([a, Assignment("DL201", runway="27L", slot_min=8, gate="A1")]).id
    d = make_wave().to_dict()
    assert {"id", "airport", "window_start", "metar", "runway_wind", "queue", "flights", "gates", "provenance"} <= d.keys()
    assert JointPlan([a]).to_dict()["id"] == JointPlan([a]).id
