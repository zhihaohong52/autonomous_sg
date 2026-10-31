"""Waves built from one row of data/challenge2/flights.csv (2015 US DOT flights): that row is the disruption
(focus flight), context flights are other arrivals at the same destination around it. Runways (OurAirports),
aircraft (FAA registry), alternates reuse atm/wave/build.py helpers; limits come from the challenge workbook
(atm/wave/constraints.py); METAR is fetched on demand from Iowa Environmental Mesonet and cached on disk.

The full flights.csv is never loaded: scripts/split_flights2015.py writes one file per destination
(data/challenge2/flights/{DEST}.csv.gz) and a light index that list_rows/_locate stream line by line."""
from __future__ import annotations

import csv
import functools
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from atm.wave import build as B
from atm.wave import constraints, wind
from atm.wave.model import (Airport, Gate, Metar, Provenance, Runway, Wave, WaveFlight)
from atm.wave.utility import TYPICAL_TAXI_MIN

CH2 = B.CH2
FLIGHTS = CH2 / "flights"
INDEX = CH2 / "flights_index.csv"
METAR_CACHE = CH2 / "metar_cache"
PREFIX = "F2015-"
IEM_TIMEOUT_S = 5
SRC_FLIGHTS = "DOT 2015 flights.csv"

_STATE_TZ = {
    "AK": "America/Anchorage", "HI": "Pacific/Honolulu", "AZ": "America/Phoenix",
    "CA": "America/Los_Angeles", "WA": "America/Los_Angeles", "OR": "America/Los_Angeles",
    "NV": "America/Los_Angeles", "ID": "America/Boise", "MT": "America/Denver", "WY": "America/Denver",
    "UT": "America/Denver", "CO": "America/Denver", "NM": "America/Denver", "ND": "America/Chicago",
    "SD": "America/Chicago", "NE": "America/Chicago", "KS": "America/Chicago", "OK": "America/Chicago",
    "TX": "America/Chicago", "MN": "America/Chicago", "IA": "America/Chicago", "MO": "America/Chicago",
    "AR": "America/Chicago", "LA": "America/Chicago", "WI": "America/Chicago", "IL": "America/Chicago",
    "MS": "America/Chicago", "AL": "America/Chicago", "TN": "America/Chicago",
    "PR": "America/Puerto_Rico", "VI": "America/Puerto_Rico", "GU": "Pacific/Guam",
}  # every other state/territory: America/New_York (split states use their majority zone)
_ICAO_REGION = {"AK": "PA", "HI": "PH", "GU": "PG", "PR": "TJ", "VI": "TI"}

_IEM_MEM: dict[tuple[str, str], tuple[float, str | None]] = {}
_IEM_RETRY_S = 120


# ---------------------------------------------------------------- index / loading

def _callsign(carrier, flight_no) -> str:
    return f"{carrier}{int(flight_no)}"


def _hhmm(v) -> str:
    m = B._hhmm_min(v)
    return f"{m // 60:02d}:{m % 60:02d}"


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _iter_index():
    with open(INDEX, newline="") as f:
        yield from csv.DictReader(f)


def _locate(row_id: int) -> dict:
    """Index row for `row_id` (index is sorted by row_id, so the scan stops at the hit)."""
    for r in _iter_index():
        rid = int(r["row_id"])
        if rid == row_id:
            return r
        if rid > row_id:
            break
    raise KeyError(f"{PREFIX}{row_id}")


@functools.lru_cache(maxsize=2)
def _dest_frame(dest: str) -> pd.DataFrame:
    p = FLIGHTS / f"{dest}.csv.gz"
    if not p.exists():
        raise KeyError(dest)
    return pd.read_csv(p, dtype={"date": str, "arr_date": str, "carrier": str, "tail": str, "origin": str,
                                 "row_id": "int32", "flight_no": "int32", "sched_dep": "int32",
                                 "sched_arr": "int32", "diverted": "int8", "cancelled": "int8"})


def list_rows(airport: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    return search(airport, q, limit, offset)[0]


def search(airport: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """Rows of flights.csv (cancelled skipped) filtered by destination and free text, as (page, total).
    Streams the index; nothing is held in memory beyond the page."""
    ap = airport.strip().upper() if airport else None
    qq = q.strip().upper() if q else None
    out: list[dict] = []
    total = 0
    for r in _iter_index():
        if r["cancelled"] == "1":
            continue
        if ap and r["dest"] != ap:
            continue
        cs = _callsign(r["carrier"], r["flight_no"])
        if qq and qq not in f"{cs} {r['tail']} {r['origin']} {r['dest']} {PREFIX}{r['row_id']}".upper():
            continue
        total += 1
        if total <= offset or len(out) >= limit:
            continue
        out.append({"row_id": int(r["row_id"]), "wave_id": f"{PREFIX}{r['row_id']}", "date": r["date"],
                    "carrier": r["carrier"], "flight": cs, "origin": r["origin"], "dest": r["dest"],
                    "sched_arr": _hhmm(r["sched_arr"]), "arr_delay": _int(r["arr_delay"]),
                    "weather_delay": _int(r["weather_delay"]),
                    "late_aircraft_delay": _int(r["late_aircraft_delay"])})
    return out, total


# ---------------------------------------------------------------- weather (IEM, on demand)

def _local_tz(iata: str) -> ZoneInfo:
    st = str(B._airports().get(iata, {}).get("STATE", ""))
    return ZoneInfo(_STATE_TZ.get(st, "America/New_York"))


def _icao(iata: str) -> str:
    st = str(B._airports().get(iata, {}).get("STATE", ""))
    if st in _ICAO_REGION:
        return _ICAO_REGION[st] + iata[1:]   # approximate outside the contiguous US
    return "K" + iata


def _http_get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=IEM_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:   # e.g. a local Python without CA certs: retry through curl (as scripts/fetch_ref.py)
        try:
            r = subprocess.run(["curl", "-sfL", "--max-time", str(IEM_TIMEOUT_S), url],
                               capture_output=True, timeout=IEM_TIMEOUT_S + 2)
            return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        except Exception:
            return None


def _fetch_iem(station: str, date: str) -> str | None:
    """IEM METAR CSV for UTC days [date-1, date+2) around the local `date` (so the last observation before
    an early-morning window is included). Memory + disk cache per (station, date); None on any failure."""
    key = (station, date)
    hit = _IEM_MEM.get(key)
    if hit is not None and (hit[1] is not None or time.time() - hit[0] < _IEM_RETRY_S):
        return hit[1]
    p = METAR_CACHE / f"{station}_{date}.csv"
    if p.exists():
        text = p.read_text()
    else:
        d0 = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)
        d1 = d0 + timedelta(days=3)
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=" + station +
               "&data=drct&data=sknt&data=gust&data=vsby&data=skyc1&data=skyl1&data=skyc2&data=skyl2"
               "&data=skyc3&data=skyl3&data=wxcodes&data=metar"
               f"&year1={d0.year}&month1={d0.month}&day1={d0.day}&year2={d1.year}&month2={d1.month}&day2={d1.day}"
               "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T&direct=no&report_type=3")
        text = _http_get(url)
        if text is not None and text.startswith("station,valid"):
            try:
                METAR_CACHE.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            except OSError:
                pass
        else:
            text = None
    _IEM_MEM[key] = (time.time(), text)
    return text


def _ceiling(r: dict) -> float | None:
    best = None
    for i in (1, 2, 3):
        cov, lvl = r.get(f"skyc{i}", "M"), r.get(f"skyl{i}", "M")
        if cov in ("BKN", "OVC", "VV") and lvl not in ("M", "", None):
            try:
                v = float(lvl)
            except ValueError:
                continue
            best = v if best is None or v < best else best
    return best


def _num_m(v) -> float | None:
    try:
        return None if v in (None, "", "M") else float(v)
    except ValueError:
        return None


def _metar(iata: str, date: str, start_local: datetime) -> Metar | None:
    text = _fetch_iem(iata, date)
    if not text:
        return None
    start_utc = start_local.replace(tzinfo=_local_tz(iata)).astimezone(timezone.utc)
    best = None
    for r in csv.DictReader(text.splitlines()):
        try:
            t = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        if t <= start_utc and (best is None or t > best[0]):
            best = (t, r)
    if best is None or start_utc - best[0] > timedelta(hours=3):
        return None
    t, r = best
    codes = [] if r.get("wxcodes") in (None, "", "M") else r["wxcodes"].split()
    return Metar(raw=r.get("metar", ""), station=r.get("station", iata), observed_at=t.strftime("%Y-%m-%dT%H:%M:00Z"),
                 wind_dir_deg=_num_m(r.get("drct")), wind_kt=_num_m(r.get("sknt")) or 0.0,
                 gust_kt=_num_m(r.get("gust")), visibility_sm=_num_m(r.get("vsby")), ceiling_ft=_ceiling(r),
                 wx_codes=codes, surface=wind.surface_from_metar(codes))


# ---------------------------------------------------------------- gates

def _gates(row_id: int, iata: str, lim: constraints.AirportLimits) -> tuple[list[Gate], str]:
    """min(gates, 12) gates; all but `spare_gates` are occupied at window start (one spare is wide-body)."""
    n = max(3, min(lim.gates, 12))
    rng = B._rng(PREFIX, str(row_id), "gates")
    real = B._gates_table().get(iata)
    gates: list[Gate] = []
    note = "generic layout A1.., W1/W2 simulated wide-body gates"
    if real:
        narrow = [r for r in real if not B._truthy(r.get("wide"))][:n - 2]
        wide = [r for r in real if B._truthy(r.get("wide"))][-2:]
        for r, size in [(r, "narrow") for r in narrow] + [(r, "wide") for r in wide]:
            ref = str(r["gate_ref"]).strip()
            gates.append(Gate(ref, size, "".join(B.itertools_takewhile_alpha(ref)).upper() or "T"))
        if len(narrow) >= 1:
            note = "gate names from OpenStreetMap" + ("" if wide else "; W1/W2 simulated wide-body gates")
    if not gates or not any(g.size == "wide" for g in gates):
        if not gates:
            gates = [Gate(f"{'AB'[i // 5]}{i % 5 + 1}", "narrow", "AB"[i // 5]) for i in range(n - 2)]
        gates += [Gate("W1", "wide", "W"), Gate("W2", "wide", "W")]
    wide_g = [g for g in gates if g.size == "wide"]
    narrow_g = [g for g in gates if g.size != "wide"]
    spare = {wide_g[0].id}
    extra = narrow_g[:]
    rng.shuffle(extra)
    spare |= {g.id for g in extra[:max(0, lim.spare_gates - 1)]}
    for g in gates:
        if g.id not in spare:
            g.busy.append({"from_min": -60, "to_min": rng.randint(15, 75), "label": "other-flight (simulated)"})
    gates.append(Gate("RS", "wide", "R", remote=True))
    return gates, note


# ---------------------------------------------------------------- flights

def _flight(row_id: int, iata: str, date: str, hour: int, r: dict, disrupted: bool, gates: list[Gate],
            used: set[str], lim: constraints.AirportLimits) -> WaveFlight:
    cs = r["callsign"]
    rng = B._rng(PREFIX, str(row_id), cs)
    tail = r["tail"] if isinstance(r.get("tail"), str) else None
    ac = B._aircraft_table().get(tail) if tail else None
    prov = [Provenance("flights", "real", f"real ({SRC_FLIGHTS})",
                       "airborne inferred from scheduled departure vs window start; DOT times are local")]
    if ac:
        atype, wake, size = ac.get("type_icao") or None, ac.get("wake") or "LARGE", ac.get("gate_size") or "narrow"
        prov.append(Provenance("aircraft", "real", "FAA registry (current)", ac.get("mfr_model")))
    else:
        atype, wake, size = None, "LARGE", "narrow"
        prov.append(Provenance("aircraft", "generic", "assumed narrow-body", "tail not in FAA registry"))

    sched_min = float(r["sched_min"])
    late, carr = float(r["late_aircraft_delay"]), float(r["carrier_delay"])
    eta_earliest = sched_min + late + carr   # weather delay is what the negotiation absorbs (as build.py)

    arr_abs = B._hhmm_min(r["sched_arr"])
    dep_abs = B._hhmm_min(r["sched_dep"])
    dep_min = dep_abs - hour * 60 - (1440 if dep_abs > arr_abs else 0)
    airborne = dep_min < -30

    cands = [g for g in gates if not g.remote and g.size == size] or [g for g in gates if not g.remote]
    pref = [g for g in cands if g.id not in used and B._gate_free(g, sched_min)] \
        or [g for g in cands if B._gate_free(g, sched_min)] or cands
    gate = pref[rng.randrange(len(pref))].id
    used.add(gate)

    alt, dist = B._nearest_alternate(iata)
    fuel = {"extra_min": constraints.RULES["spare_holding_min"], "alternate": alt,
            "alternate_min": int(dist / 7 + 10), "final_reserve_min": constraints.RULES["final_reserve_min"]}
    conns = [{"flight": f"{r['carrier']}{rng.randint(100, 2999)}", "pax": rng.randint(5, 40),
              "departs_min": sched_min + rng.randint(40, 120)} for _ in range(rng.randint(0, 3))]

    turn = float(lim.min_turnaround_min)
    shift = constraints.RULES["ground_shift_h"] * 60
    elapsed = rng.random() * shift
    floor = eta_earliest + TYPICAL_TAXI_MIN + turn   # never infeasible by construction (as build.py)
    crew_end = max(sched_min + shift - elapsed, floor)
    duty_end = dep_min - 60 + constraints.RULES["crew_duty_h"] * 60
    prov += [Provenance("fuel", "simulated", constraints.SOURCE,
                        f"spare fuel on arrival = {constraints.RULES['spare_holding_min']} min holding; "
                        f"final reserve {constraints.RULES['final_reserve_min']} min"),
             Provenance("crew", "simulated", constraints.SOURCE,
                        f"ground shift {constraints.RULES['ground_shift_h']} h, elapsed at sched arrival "
                        f"{elapsed:.0f} min (seeded), floored at earliest turnaround end; flight crew report = "
                        f"sched dep - 60 min, {constraints.RULES['crew_duty_h']} h duty ends T{duty_end:+.0f} min"),
             Provenance("connections", "simulated", "seeded model")]
    return WaveFlight(
        callsign=cs, carrier=r["carrier"], airline=B._airlines().get(r["carrier"], r["carrier"]),
        flight_no=int(r["flight_no"]), tail=tail, aircraft_type=atype, wake=wake, gate_size=size,
        origin=str(r["origin"]), sched_arr=_sched_iso(date, B._hhmm_min(r["sched_arr"])),
        sched_min=sched_min, eta_earliest_min=eta_earliest, airborne=airborne, disrupted=disrupted,
        delays={"weather": float(r["weather_delay"]), "late_aircraft": late, "nas": float(r["nas_delay"]),
                "carrier": carr, "arr_delay": float(r["arr_delay"])},
        fuel=fuel, connections=conns, scheduled_gate=gate, crew_shift_end_min=crew_end,
        next_departure_min=sched_min + turn + rng.randint(0, 75), provenance=prov)


# ---------------------------------------------------------------- public API

def _sched_iso(date: str, minutes_abs: int) -> str:
    return f"{date}T{minutes_abs // 60:02d}:{minutes_abs % 60:02d}:00"


def build_from_row(row_id: int) -> Wave:
    row = _locate(int(row_id))
    if row["cancelled"] == "1":
        raise KeyError(f"{PREFIX}{row_id}")
    iata, date = row["dest"], row["arr_date"]
    df = _dest_frame(iata)
    df = df[(df["arr_date"] == date) & (df["cancelled"] == 0)]
    focus_abs = B._hhmm_min(row["sched_arr"])
    hour = focus_abs // 60
    mins = df["sched_arr"].map(B._hhmm_min)
    pool = df[(mins >= hour * 60) & (mins < hour * 60 + 60)]
    for span in (60, 120, 240):   # sparse hour (flights.csv is filtered to delayed flights): widen around focus
        if (pool["row_id"] != int(row_id)).sum() >= 3:
            break
        pool = df[(mins - focus_abs).abs() <= span]
    pool = pool[(pool["diverted"] == 0) | (pool["row_id"] == int(row_id))].copy()
    for c in ("carrier", "origin", "tail", "date"):
        pool[c] = pool[c].astype(object)
    pool["sched_min"] = (pool["sched_arr"].map(B._hhmm_min) - hour * 60).astype(float)
    pool["callsign"] = pool["carrier"] + pool["flight_no"].astype(int).astype(str)
    pool["_focus"] = (pool["row_id"] == int(row_id))
    focus_min = float(focus_abs - hour * 60)
    pool["_d"] = (pool["sched_min"] - focus_min).abs()
    pool = pool.sort_values(["_focus", "_d", "row_id"], ascending=[False, True, True], kind="stable") \
               .drop_duplicates("callsign")
    neg, rest = pool.head(4), pool.iloc[4:].sort_values("sched_min").head(15)
    focus_cs = _callsign(row["carrier"], row["flight_no"])

    lim = constraints.airport(iata)
    runways = B._runways(iata)
    start_local = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=hour)
    metar = _metar(iata, date, start_local)
    B._close_unsafe(runways, wind.runway_wind(metar, runways))
    rwinds = wind.runway_wind(metar, runways)

    gates, gate_note = _gates(int(row_id), iata, lim)
    used: set[str] = set()
    wid = f"{PREFIX}{int(row_id)}"
    neg = neg.sort_values("sched_min", kind="stable")
    flights = [_flight(int(row_id), iata, date, hour, r, r["callsign"] == focus_cs, gates, used, lim)
               for r in neg.to_dict("records")]

    ap = B._airports().get(iata, {})
    coords = B._airport_coords(iata) or (0.0, 0.0)
    airport = Airport(iata=iata, icao=_icao(iata), name=str(ap.get("AIRPORT", iata)),
                      lat=coords[0], lon=coords[1], runways=runways)
    limits = (f"{lim.size_class}: {lim.active_runways} runways, {lim.gates} gates (wave uses "
              f"{len(gates) - 1}), {lim.max_runway_ops_per_hour} ops/h, turnaround {lim.min_turnaround_min} min, "
              f"{lim.spare_gates} spare gates")
    prov = [
        Provenance("flights", "real", f"real ({SRC_FLIGHTS})", f"row {row_id}"),
        Provenance("queue", "real", f"real ({SRC_FLIGHTS})"),
        Provenance("runways", "real", "OurAirports"),
        (Provenance("weather", "real", "IEM METAR", metar.raw) if metar
         else Provenance("weather", "generic", "no METAR available (IEM unreachable or no observation)")),
        Provenance("gates", "simulated", constraints.SOURCE, f"{limits}; {gate_note}"),
        Provenance("gate_occupancy", "simulated", constraints.SOURCE, f"only {lim.spare_gates} gates free at "
                   f"window start; seed={wid}"),
    ]
    return Wave(id=wid, airport=airport, window_start=f"{date}T{hour:02d}:00:00", metar=metar,
                runway_wind=rwinds, queue=B._queue(rest, runways, rwinds), flights=flights, gates=gates,
                provenance=prov, focus=focus_cs)
