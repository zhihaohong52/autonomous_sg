import pytest

from atm.wave import flights2015 as F


def test_airport_without_runways_is_skipped_and_unbuildable():
    assert not F._supported("CID")
    assert F.list_rows(airport="CID", limit=5) == []
    with pytest.raises(KeyError, match="unsupported airport"):
        F.build_from_row(310)


def test_supported_airport_builds():
    row = F.list_rows(airport="ATL", limit=1)[0]
    assert row["dest"] == "ATL" and F._supported("ATL")
    assert F.build_from_row(row["row_id"]).id == row["wave_id"]


def test_weather_and_nas_delay_reach_eta_earliest():
    from atm.wave import build
    from atm.wave.graph import negotiate_wave
    w = build.build_wave("F2015-293482")
    f = w.flights[0] if len(w.flights) == 1 else next(x for x in w.flights if x.delays.get("weather"))
    d = f.delays
    assert d["weather"] > 0
    assert f.eta_earliest_min == pytest.approx(f.sched_min + d["late_aircraft"] + d["carrier"] + d["weather"] + d["nas"])
    assert negotiate_wave(w, "formulas")["revision"]["delay_vs_schedule_min"] > 0


def test_pilot_does_not_divert_for_gate_conflict():
    from atm.wave import build
    from atm.wave.graph import negotiate_wave
    r = negotiate_wave(build.build_wave("F2015-200000"), "formulas")
    labels = [m["move"]["label"] for x in r["log"] if x["body"]["type"] == "AGENT_TURN"
              for m in x["body"]["moves"]]
    assert not any("diversion" in l for l in labels)
