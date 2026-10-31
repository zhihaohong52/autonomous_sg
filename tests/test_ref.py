import csv
from pathlib import Path

from atm.wave.hubs import HUBS

REF = Path(__file__).resolve().parent.parent / "data" / "ref"


def _rows(name):
    with open(REF / name, newline="") as f:
        return list(csv.DictReader(f))


def test_ref_coverage():
    rw = _rows("runways.csv")
    for h in HUBS:
        assert sum(r["airport"] == h for r in rw) >= 2, f"runways missing for {h}"
    assert len({r["airport"] for r in _rows("metar.csv")} & set(HUBS)) >= 28
    ac = _rows("aircraft.csv")
    assert len(ac) >= 3000
    assert {r["wake"] for r in ac} <= {"SUPER", "HEAVY", "B757", "LARGE", "SMALL"}
    assert {r["gate_size"] for r in ac} <= {"narrow", "wide"}
    assert (REF / "gates.csv").exists()
