import json
import time

import pytest

from atm.wave import build
from atm.wave.model import Metar

pytestmark = pytest.mark.skipif(
    not (build.WAVES / "index.csv").exists() or not (build.WAVES / "arrivals.csv.gz").exists(),
    reason="wave data files not present")


def _first_id():
    return build.list_waves(limit=1)[0]["id"]


def test_build_known_wave():
    idx = build._index()
    w = build.build_wave(idx.iloc[0]["wave_id"])
    assert 3 <= len(w.flights) <= 5
    assert len(w.airport.runways) >= 2
    assert any(f.disrupted for f in w.flights)
    assert any(g.remote for g in w.gates)
    json.dumps(w.to_dict())
    # a hub with METAR must have one
    with_metar = [i for i in idx["wave_id"].head(30) if build.build_wave(i).metar is not None]
    assert with_metar


def test_unknown_tail_generic(monkeypatch):
    monkeypatch.setattr(build, "_aircraft_table", lambda: {})
    w = build.build_wave(_first_id())
    for f in w.flights:
        assert f.aircraft_type is None and f.wake == "LARGE" and f.gate_size == "narrow"
        ac = next(p for p in f.provenance if p.layer == "aircraft")
        assert ac.status == "generic" and ac.detail == "tail not in FAA registry"


def test_deterministic():
    wid = _first_id()
    assert build.build_wave(wid).to_dict() == build.build_wave(wid).to_dict()


def test_list_waves_shape():
    out = build.list_waves(limit=5)
    assert 0 < len(out) <= 5
    keys = {"id", "airport", "window_start", "window_end", "n_flights", "n_queue", "callsigns",
            "disrupted_callsigns", "weather_summary", "replay_available"}
    for s in out:
        assert set(s) == keys
        assert set(s["airport"]) == {"iata", "icao", "name"}
        assert s["n_flights"] == len(s["callsigns"])
        assert set(s["disrupted_callsigns"]) <= set(s["callsigns"])
    assert set(build.random_wave(seed=1)) == keys
    ap = out[0]["airport"]["iata"]
    assert all(s["airport"]["iata"] == ap for s in build.list_waves(airport=ap, limit=5))
    assert build.list_waves(q=out[0]["id"])[0]["id"] == out[0]["id"]


def test_unknown_wave_keyerror():
    with pytest.raises(KeyError):
        build.build_wave("ZZZ-2024-07-15-16")
    with pytest.raises(KeyError):
        build.build_wave("garbage")


def test_build_speed():
    wid = _first_id()
    build.build_wave(wid)
    t = time.perf_counter()
    build.build_wave(wid)
    assert time.perf_counter() - t < 0.3


def test_runway_closed_reason_cites_faa(monkeypatch):
    m = Metar(raw="TEST 36040G55KT", station="TEST", observed_at="2024-07-15T15:51:00Z",
              wind_dir_deg=360, wind_kt=40, gust_kt=55, visibility_sm=10, ceiling_ft=None,
              wx_codes=[], surface="dry")
    monkeypatch.setattr(build, "_metar_for", lambda *a: m)
    w = build.build_wave(_first_id())
    closed = [r for r in w.airport.runways if not r.open]
    assert closed
    assert all("FAA Order 8400.9" in r.closed_reason for r in closed)
    assert any(r.closed_reason.startswith("crosswind") for r in closed)


def test_crew_shift_not_infeasible_by_construction():
    """Crew shift end is anchored to the delayed ETA: landing at the earliest ETA with typical taxi
    never breaches GND-CREW-DUTY (regression: ~70% of real flights did before any move)."""
    from atm.wave.utility import TURNAROUND_MIN, TYPICAL_TAXI_MIN
    idx = build._index()
    for wid in idx["wave_id"].head(25):
        for f in build.build_wave(wid).flights:
            assert f.crew_shift_end_min >= f.eta_earliest_min + TYPICAL_TAXI_MIN + TURNAROUND_MIN
            assert f.crew_shift_end_min <= f.eta_earliest_min + TYPICAL_TAXI_MIN + TURNAROUND_MIN + 90


def test_wide_body_gate_exists():
    idx = build._index()
    for wid in idx["wave_id"].head(25):
        assert any(g.size == "wide" and not g.remote for g in build.build_wave(wid).gates)
