"""Builds the airport state around one real disrupted flight.

The DOT/BTS record gives us the flight, its delays and the destination weather. It has no
runway queue, gate plan, fuel state or crew roster, so those are SYNTHESISED here, seeded by
the flight id so every run of the same flight is reproducible. Everything synthetic is
tagged in `Scenario.synthetic_fields` so the UI and the pitch can say so.

Time convention: minutes relative to the flight's scheduled arrival (t=0).
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "challenge2"

# Wake categories (ICAO-style, simplified) and minimum time separation in minutes,
# keyed (leader, follower). Converted from distance minima at ~140 kt final approach speed.
WAKE_SEP_MIN = {
    ("H", "H"): 2.0, ("H", "M"): 2.5, ("H", "L"): 3.0,
    ("M", "H"): 1.5, ("M", "M"): 1.5, ("M", "L"): 2.5,
    ("L", "H"): 1.5, ("L", "M"): 1.5, ("L", "L"): 1.5,
}
HOLD_BURN_KG_MIN = {"H": 95.0, "M": 40.0}
TAXI_MIN = {"near": 5, "far": 11}
TURNAROUND_MIN = {"narrow": 40, "wide": 75}
FINAL_RESERVE_MIN = 30  # FAR 121.639-style final reserve


@dataclass
class Arrival:
    callsign: str
    runway: str
    time: float
    wake: str


@dataclass
class Gate:
    id: str
    size: str  # narrow | wide
    pier: str  # A (near runway 1) | B (near runway 2)
    busy: list[tuple[float, float]]  # occupied intervals


@dataclass
class Connection:
    flight: str
    pax: int
    departs: float  # minutes rel. to t=0


@dataclass
class Scenario:
    flight: dict
    callsign: str
    wake: str
    gate_size: str
    disruption: list[str]
    weather: dict
    runways: dict[str, dict]  # id -> {open, capacity_per_hr, reason}
    blocked_taxiway: dict | None  # {id, cuts: (runway, pier)}
    arrivals: list[Arrival]
    gates: list[Gate]
    scheduled_gate: str
    eta_earliest: float
    fuel: dict  # extra_min, burn_kg_min, alternate, alternate_min
    crew_shift_end: float
    crew_max_overtime: float
    next_departure: float  # this tail's next scheduled departure
    connections: list[Connection]
    synthetic_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def disruptions() -> pd.DataFrame:
    return pd.read_csv(DATA / "disruptions.csv")


@lru_cache(maxsize=1)
def airports() -> pd.DataFrame:
    return pd.read_csv(DATA / "airports.csv").set_index("IATA_CODE")


@lru_cache(maxsize=1)
def airlines() -> dict[str, str]:
    return pd.read_csv(DATA / "airlines.csv").set_index("IATA_CODE")["AIRLINE"].to_dict()


def _nearest_alternate(dest: str) -> tuple[str, float]:
    ap = airports()
    if dest not in ap.index:
        return "ALT", 25.0
    lat, lon = ap.loc[dest, ["LATITUDE", "LONGITUDE"]]
    if pd.isna(lat) or pd.isna(lon):
        return "ALT", 25.0
    best, best_d = "ALT", 1e9
    for code, row in ap.iterrows():
        if code == dest or pd.isna(row.LATITUDE):
            continue
        d = math.dist((lat, lon), (row.LATITUDE, row.LONGITUDE))
        if 0.4 < d < best_d:  # skip co-located fields
            best, best_d = code, d
    # ~1 degree ~ 60 nm; cruise-to-alternate at ~6 nm/min plus approach
    return best, round(best_d * 60 / 6 + 12, 1)


def build(flight_id: int) -> Scenario:
    row = disruptions().set_index("id").loc[flight_id]
    rng = random.Random(int(hashlib.sha256(str(flight_id).encode()).hexdigest(), 16) % 2**32)
    f = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
    f["id"] = int(flight_id)
    f["airline"] = airlines().get(f["carrier"], f["carrier"])

    wd, lad = f["weather_delay"] or 0, f["late_aircraft_delay"] or 0
    wind = f.get("dest_wind_kmh") or rng.uniform(10, 30)
    gust = f.get("dest_gust_kmh") or 0
    precip = f.get("dest_precip_mm") or 0
    disruption = []
    if wd > 15:
        disruption.append(f"weather ({wd:.0f} min weather delay reported)")
    if lad > 20:
        disruption.append(f"late inbound aircraft ({lad:.0f} min late-aircraft delay reported)")

    wake = "H" if f["distance"] > 2000 else "M"
    gate_size = "wide" if wake == "H" else "narrow"
    callsign = f"{f['carrier']}{f['flight_no']}"

    # Runways: weather cuts arrival capacity; heavy weather can close one runway.
    severity = min(1.0, wd / 90 + gust / 80 + precip / 10)
    cap = round(40 - 18 * severity)
    r2_closed = severity > 0.55 or rng.random() < 0.15
    runways = {
        "R1": {"open": True, "capacity_per_hr": cap, "reason": "open"},
        "R2": {"open": not r2_closed, "capacity_per_hr": 0 if r2_closed else cap,
               "reason": "closed: convective weather / braking action" if r2_closed else "open"},
    }
    # Weather stretches separation (lower acceptance rate).
    sep_factor = 1 + 0.6 * severity

    # Other inbound traffic around our slot.
    arrivals = []
    for rwy in ("R1", "R2"):
        if not runways[rwy]["open"]:
            continue
        t = rng.uniform(-6, -1)
        for i in range(int(18 + 10 * severity)):
            w = "H" if rng.random() < 0.15 else "M"
            arrivals.append(Arrival(f"{rng.choice(['DAL','AAL','UAL','SWA','SKW','JBU'])}{rng.randint(100, 3999)}",
                                    rwy, round(t, 1), w))
            t += WAKE_SEP_MIN[("M", w)] * sep_factor + rng.choice([0, 0, 0.5, 1, 2.5, 4])
    eta_earliest = round(rng.uniform(0, 4) + (min(wd, 25) if wd > 15 else 0) * 0.2, 1)

    # Gates: two piers; scheduled gate is busy longer when the inbound tail is late.
    gates = []
    for i in range(1, 11):
        pier = "A" if i <= 5 else "B"
        size = "wide" if i in (5, 10) else "narrow"
        busy, t = [], rng.uniform(-60, 0)
        while t < 240:
            dur = rng.uniform(35, 80)
            if rng.random() < 0.7:
                busy.append((round(t, 1), round(t + dur, 1)))
            t += dur + rng.uniform(5, 40)
        gates.append(Gate(f"{pier}{i if pier == 'A' else i - 5}", size, pier, busy))
    # Remote stand: always available, passengers bussed (slow, costly for ground ops).
    gates.append(Gate("RS", "wide", "R", []))
    same_size = [g for g in gates if g.size == gate_size and g.pier != "R"]
    sched = rng.choice(same_size)
    # Our scheduled gate is held by the outbound we were meant to replace; late-aircraft
    # disruption means that outbound pushes late.
    hold_until = round(rng.uniform(10, 25) + min(lad, 60) * 0.5, 1)
    sched.busy = [(a, b) for a, b in sched.busy if b < -5 or a > hold_until + 50] + [(-40.0, hold_until)]

    blocked = None
    if rng.random() < 0.6:
        cut = rng.choice([("R1", "B"), ("R2", "A")])
        blocked = {"id": rng.choice(["T4", "K2", "M1"]), "cuts": list(cut),
                   "reason": rng.choice(["tug breakdown", "fuel spill clean-up", "lighting failure"])}

    alt, alt_min = _nearest_alternate(f["dest"])
    fuel = {"extra_min": round(rng.uniform(14, 45), 1), "burn_kg_min": HOLD_BURN_KG_MIN[wake],
            "alternate": alt, "alternate_min": alt_min, "final_reserve_min": FINAL_RESERVE_MIN}

    conns = [Connection(f"{f['carrier']}{rng.randint(100, 4999)}", rng.randint(4, 40), round(rng.uniform(35, 140), 0))
             for _ in range(rng.randint(2, 5))]

    return Scenario(
        flight=f, callsign=callsign, wake=wake, gate_size=gate_size, disruption=disruption,
        weather={"wind_kmh": round(wind, 1), "gust_kmh": gust, "precip_mm": precip,
                 "severity": round(severity, 2), "separation_factor": round(sep_factor, 2)},
        runways=runways, blocked_taxiway=blocked, arrivals=arrivals, gates=gates,
        scheduled_gate=sched.id, eta_earliest=eta_earliest, fuel=fuel,
        crew_shift_end=round(rng.uniform(60, 150), 0), crew_max_overtime=45,
        next_departure=round(rng.uniform(70, 130), 0), connections=conns,
        synthetic_fields=["runway queue", "runway closure", "gate plan", "taxiway closure",
                          "fuel state", "crew shift", "passenger connections", "next departure"],
    )
