"""Operational limits from the challenge workbook (data/challenge2/challenge2_operational_constraints.xlsx):
airport capacity, wake separation, fuel/crew rules. Parsed once; hard-coded fallbacks equal the workbook
values so the server still runs if the file or openpyxl is missing. The limits are synthetic planning
numbers supplied by the challenge, not real airport data."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

XLSX = Path(__file__).resolve().parent.parent.parent / "data" / "challenge2" / "challenge2_operational_constraints.xlsx"
SOURCE = "challenge2_operational_constraints.xlsx (synthetic planning limits)"

RULES = {
    "final_reserve_min": 30, "contingency_pct": 5,
    "burn_kg_h": {"narrow": 2400, "wide": 6500},
    "spare_holding_min": 45, "crew_duty_h": 13, "ground_shift_h": 8, "overtime_mult": 1.5,
    "gate_notice_min": 20,
}


@dataclass(frozen=True)
class AirportLimits:
    iata: str
    size_class: str
    active_runways: int
    gates: int
    max_runway_ops_per_hour: int
    min_turnaround_min: int
    spare_gates: int


# size_class -> (active_runways, gates, max_runway_ops_per_hour, min_turnaround_min, spare_gates)
_CLASS = {"Large hub": (4, 150, 90, 45, 3), "Medium hub": (3, 70, 60, 35, 3),
          "Small hub": (2, 30, 36, 35, 2), "Non-hub": (1, 8, 20, 35, 2)}
_LARGE = "ORD ATL DFW DEN LAX".split()
_MEDIUM = ("SFO LAS LGA IAH MCO PHX EWR BOS BWI MDW JFK DTW MSP MIA SAN DAL TPA CLT DCA SEA FLL HOU PHL").split()
_SMALL = ("SLC STL BNA OAK AUS SJC SMF MSY MCI RDU PDX IAD SNA MKE").split()  # fallback lists only the biggest

# wake: (leader, follower) -> (nm, seconds); anything else is "Any other pair"
_WAKE = {("Super", "Heavy"): (6, 180), ("Super", "Large"): (7, 180), ("Super", "Small"): (8, 180),
         ("Heavy", "Heavy"): (4, 120), ("Heavy", "Large"): (5, 120), ("Heavy", "Small"): (6, 120),
         ("Large", "Small"): (4, 60)}
_OTHER = (3, 60)
# model.Wake -> workbook category. B757 has no workbook row; treated as Heavy (conservative).
_CAT = {"SUPER": "Super", "HEAVY": "Heavy", "B757": "Heavy", "LARGE": "Large", "SMALL": "Small"}


def _fallback_airports() -> dict[str, tuple]:
    out = {}
    for lst, cls in ((_LARGE, "Large hub"), (_MEDIUM, "Medium hub"), (_SMALL, "Small hub")):
        for i in lst:
            out[i] = (cls,) + _CLASS[cls]
    return out


@functools.lru_cache(maxsize=1)
def _load() -> tuple[dict, dict]:
    """({iata: (size_class, runways, gates, ops, turnaround, spare)}, {(leader, follower): (nm, s)})"""
    airports, wake = _fallback_airports(), dict(_WAKE)
    try:
        import openpyxl
        wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
        parsed = {}
        for r in wb["airport_capacity"].iter_rows(min_row=4, values_only=True):
            if r[0] and r[5] and r[6] is not None:
                parsed[str(r[0])] = (str(r[5]), int(r[6]), int(r[7]), int(r[8]), int(r[9]), int(r[10]))
        pw = {}
        for r in wb["wake_separation"].iter_rows(min_row=4, values_only=True):
            if r[0] and r[1] and r[2] is not None:
                pw[(str(r[0]).split()[0], str(r[1]).split()[0])] = (float(r[2]), int(r[3]))
        wb.close()
        if parsed:
            airports = parsed
        if pw:
            wake = pw
    except Exception:  # missing workbook / openpyxl: hard-coded values above are the same numbers
        pass
    return airports, wake


def airport(iata: str) -> AirportLimits:
    airports, _ = _load()
    row = airports.get(iata.upper())
    if row is None:
        row = ("unlisted",) + _CLASS["Non-hub"]  # smallest size class
    return AirportLimits(iata.upper(), *row)


def _cat(c: str) -> str:
    return _CAT.get(str(c).upper(), str(c).title())


def wake_nm(leader: str, follower: str) -> float:
    return _load()[1].get((_cat(leader), _cat(follower)), _OTHER)[0]


def wake_s(leader_cat: str, follower_cat: str) -> int:
    """Minimum time separation in seconds (workbook `min_time_s`). Accepts model Wake strings
    (SUPER/HEAVY/B757/LARGE/SMALL) or workbook names (Super/Heavy/Large/Small)."""
    return int(_load()[1].get((_cat(leader_cat), _cat(follower_cat)), _OTHER)[1])
