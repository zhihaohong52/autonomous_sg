"""Runway wind components and crosswind/tailwind limits for wave mode.

Limits follow FAA Order 8400.9 §6d, tightened per aircraft type where the
manufacturer demonstrated crosswind is lower.
"""
from __future__ import annotations

import math

from atm.wave.model import Metar, Runway, RunwayWind

# Manufacturer demonstrated crosswind (kt), approximate. Keys are ICAO type designators.
TYPE_XWIND_KT: dict[str, int] = {
    "A318": 38, "A319": 38, "A320": 38, "A321": 38,
    "B737": 33, "B738": 33, "B739": 33, "B38M": 33, "B39M": 33,
    "B752": 40, "B753": 40, "B763": 40, "B764": 40,
    "B772": 38, "B77W": 38,
    "B788": 40, "B789": 40, "B78X": 40,
    "A332": 40, "A333": 40, "A339": 40, "A359": 40,
    "E170": 30, "E175": 30, "E190": 30, "E145": 30,
    "CRJ7": 27, "CRJ9": 27,
    "BCS1": 30, "BCS3": 30,
    "A388": 38,
}
TYPE_XWIND_SOURCE = "manufacturer demonstrated crosswind (FCOM/AFM, approx.)"

VRB_CALM_KT = 6.0  # VRB at or below this speed (gust included) is treated as calm

WET_CODES = ("RA", "DZ", "SN", "TS", "GR", "PL", "FZ", "SH")


def components(wind_dir: float | None, wind_kt: float, gust_kt: float | None,
               heading_true: float) -> tuple[float, float, float]:
    """Return (headwind, crosswind, tailwind) in kt, each >= 0, rounded to 1 decimal."""
    speed = max(wind_kt, gust_kt or 0)
    if wind_dir is None:
        # Variable / missing direction. VRB is reported for light winds (<= 6 kt) or for winds
        # varying across >= 180 deg; it applies to every runway equally, so treating it as a full
        # tailwind closed ALL runways (e.g. VRB06KT). Light VRB is treated as calm; stronger VRB
        # gets the worst-case crosswind (gust if gusting) but no tailwind (the runway in use is
        # chosen into the prevailing component).
        if speed <= VRB_CALM_KT:
            return 0.0, 0.0, 0.0
        return 0.0, round(speed, 1), 0.0
    angle = math.radians(wind_dir - heading_true)
    crosswind = abs(speed * math.sin(angle))
    along = math.cos(angle)
    if along >= 0:
        headwind, tailwind = wind_kt * along, 0.0
    else:
        headwind, tailwind = 0.0, abs(speed * along)
    return round(headwind, 1), round(crosswind, 1), round(tailwind, 1)


def limits(surface: str, aircraft_type: str | None) -> tuple[float, float, str]:
    """Return (limit_crosswind, limit_tailwind, source) for a surface and aircraft type."""
    if surface == "wet":
        limit_xw, limit_tw, source = 15.0, 0.0, "FAA Order 8400.9 §6d (wet)"
    else:
        limit_xw, limit_tw, source = 20.0, 5.0, "FAA Order 8400.9 §6d (dry)"
    type_xw = TYPE_XWIND_KT.get(aircraft_type) if aircraft_type else None
    if type_xw is not None and type_xw < limit_xw:
        limit_xw = float(type_xw)
        source = f"{aircraft_type} demonstrated crosswind {type_xw} kt (stricter than FAA 8400.9 §6d {surface})"
    return limit_xw, limit_tw, source


def within_limits(crosswind: float, tailwind: float, limit_xw: float, limit_tw: float) -> bool:
    # 8400.9: calm winds of 0-3 kt are not treated as a tailwind.
    return crosswind <= limit_xw and tailwind <= (3 if limit_tw == 0 else limit_tw)


def surface_from_metar(wx_codes: list[str]) -> str:
    if any(any(c in code for c in WET_CODES) for code in wx_codes):
        return "wet"
    return "dry"


def runway_wind(metar: Metar | None, runways: list[Runway],
                aircraft_type: str | None = None) -> list[RunwayWind]:
    if metar is None:
        return []
    out = []
    for r in runways:
        hw, xw, tw = components(metar.wind_dir_deg, metar.wind_kt, metar.gust_kt, r.heading_true)
        lim_xw, lim_tw, source = limits(metar.surface, aircraft_type)
        out.append(RunwayWind(
            runway=r.id, headwind_kt=hw, crosswind_kt=xw, tailwind_kt=tw,
            limit_crosswind_kt=lim_xw, limit_tailwind_kt=lim_tw, limit_source=source,
            within_limits=within_limits(xw, tw, lim_xw, lim_tw)))
    return out
