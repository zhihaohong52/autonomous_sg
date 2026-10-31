"""Synthetic ATL-like wave for unit tests (no data files needed)."""
from __future__ import annotations

from dataclasses import replace

from atm.wave.model import (Airport, Gate, Metar, Provenance, QueueArrival, Runway, Wave, WaveFlight)


def _flight(cs, typ, wake, size, sched, airborne=True, disrupted=False, extra=30, conn=None, gate="A1"):
    return WaveFlight(
        callsign=cs, carrier=cs[:2], airline=cs[:2], flight_no=int(cs[2:]), tail=None,
        aircraft_type=typ, wake=wake, gate_size=size, origin="ORD",
        sched_arr=f"2024-07-15T16:{int(sched):02d}:00", sched_min=sched, eta_earliest_min=sched,
        airborne=airborne, disrupted=disrupted,
        delays={"weather": 40 if disrupted else 0, "late_aircraft": 0, "nas": 0, "carrier": 0,
                "arr_delay": 40 if disrupted else 0},
        fuel={"extra_min": extra, "alternate": "BHM", "alternate_min": 35, "final_reserve_min": 30},
        connections=conn or [{"flight": "DL900", "pax": 12, "departs_min": sched + 60}],
        scheduled_gate=gate, crew_shift_end_min=sched + 90, next_departure_min=sched + 50,
        provenance=[Provenance("flights", "real", "test fixture")])


def make_wave(**overrides) -> Wave:
    runways = [Runway("08L", 94, 9000), Runway("26R", 274, 9000),
               Runway("09R", 94, 9000), Runway("27L", 274, 9000)]
    metar = Metar(raw="KATL 151551Z 27015KT 10SM FEW040 30/20 A2992", station="KATL",
                  observed_at="2024-07-15T15:51:00Z", wind_dir_deg=270, wind_kt=15, gust_kt=None,
                  visibility_sm=10, ceiling_ft=None, wx_codes=[], surface="dry")
    flights = [
        _flight("DL100", "B772", "HEAVY", "wide", 5, disrupted=True, gate="A5"),
        _flight("DL201", "A321", "LARGE", "narrow", 6, gate="A1"),
        _flight("AA302", "B738", "LARGE", "narrow", 8, extra=16, gate="A2"),
        _flight("UA403", "A320", "LARGE", "narrow", 12, airborne=False, gate="B1"),
    ]
    gates = [Gate("A1", "narrow", "A"), Gate("A2", "narrow", "A"), Gate("A5", "wide", "A"),
             Gate("B1", "narrow", "B"), Gate("B2", "narrow", "B", busy=[{"from_min": 0, "to_min": 40, "label": "DL55"}]),
             Gate("B5", "wide", "B"), Gate("RS", "wide", "R", remote=True)]
    queue = [QueueArrival("WN1", "B737", "LARGE", "26R", 2), QueueArrival("DL7", "A321", "LARGE", "27L", 4),
             QueueArrival("F99", "A320", "LARGE", "26R", 10)]
    w = Wave(id="ATL-2024-07-15-16",
             airport=Airport("ATL", "KATL", "Hartsfield-Jackson Atlanta Intl", 33.64, -84.43, runways),
             window_start="2024-07-15T16:00:00", metar=metar, runway_wind=[], queue=queue,
             flights=flights, gates=gates, provenance=[Provenance("weather", "real", "test fixture")])
    return replace(w, **overrides)
