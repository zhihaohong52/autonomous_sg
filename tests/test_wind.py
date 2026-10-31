from __future__ import annotations

import pytest

from atm.wave.wind import TYPE_XWIND_KT, components, limits, runway_wind, surface_from_metar, within_limits
from tests.fixtures import make_wave


def test_crosswind_90deg():
    assert components(270, 20, None, 360) == (0.0, 20.0, 0.0)


def test_tailwind():
    assert components(90, 10, None, 270)[2] == 10.0


def test_gust_used():
    assert components(270, 15, 25, 360)[1] == 25.0


def test_faa_wet_limits():
    lim_xw, lim_tw, source = limits("wet", None)
    assert (lim_xw, lim_tw) == (15, 0)
    assert "8400.9" in source


def test_faa_dry():
    assert limits("dry", "A320")[:2] == (20, 5)


def test_type_stricter(monkeypatch):
    monkeypatch.setitem(TYPE_XWIND_KT, "TEST", 12)
    lim_xw, _, source = limits("dry", "TEST")
    assert lim_xw == 12
    assert "TEST" in source


def test_vrb_worst_case():
    assert components(None, 12, 18, 90) == (0, 18, 18)


def test_calm_tailwind_wet_ok():
    assert within_limits(5, 2, 15, 0) is True
    assert within_limits(5, 4, 15, 0) is False


def test_surface_from_metar():
    assert surface_from_metar(["-RA"]) == "wet"
    assert surface_from_metar(["FZRA"]) == "wet"
    assert surface_from_metar([]) == "dry"


def test_runway_wind_none_metar():
    assert runway_wind(None, make_wave().airport.runways) == []


def test_runway_wind_from_fixture():
    w = make_wave()
    rw = runway_wind(w.metar, w.airport.runways)
    assert len(rw) == 4
    by_id = {r.runway: r for r in rw}
    assert by_id["26R"].headwind_kt == pytest.approx(15, abs=0.1)
    assert by_id["26R"].within_limits is True
    assert by_id["08L"].tailwind_kt == pytest.approx(15, abs=0.1)
    assert by_id["08L"].within_limits is False
