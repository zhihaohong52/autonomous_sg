"""Build a `Wave` (contract WaveScenario) and WaveSummary dicts from real BTS / METAR /
OurAirports / FAA data, plus seeded simulation of the layers no public source has
(fuel, crew, connections, gate occupancy). Deterministic: same wave_id -> same Wave."""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from atm.wave import wind
from atm.wave.utility import TURNAROUND_MIN, TYPICAL_TAXI_MIN
from atm.wave.model import (Airport, Gate, Metar, Provenance, QueueArrival, Runway, Wave,
                            WaveFlight)

ROOT = Path(__file__).resolve().parent.parent.parent
WAVES = ROOT / "data" / "waves"
REF = ROOT / "data" / "ref"
REPLAY = ROOT / "data" / "replay"
CH2 = ROOT / "data" / "challenge2"

_CACHE: dict = {}


# ---------------------------------------------------------------- loading

def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _index() -> pd.DataFrame:
    def load():
        df = pd.read_csv(WAVES / "index.csv", dtype={"date": str})
        # index.csv writes flight numbers as floats ("AA807.0"); normalise to callsigns
        df["disrupted_flights"] = df["disrupted_flights"].fillna("").str.replace(r"\.0(?=;|$)", "", regex=True)
        return df
    return _cached("index", load)


def _arrivals():
    """(frame, {(dest, date): row positions}) so per-wave filtering is O(window)."""
    def load():
        df = pd.read_csv(WAVES / "arrivals.csv.gz", dtype={"date": str, "tail": str, "carrier": str})
        df = df.dropna(subset=["sched_arr", "carrier", "flight_no"]).reset_index(drop=True)
        for c in ("weather_delay", "nas_delay", "late_aircraft_delay", "carrier_delay", "arr_delay"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df["cancelled"] = pd.to_numeric(df["cancelled"], errors="coerce").fillna(0) > 0
        return df, df.groupby(["dest", "date"]).indices
    return _cached("arrivals", load)


def _runways_by_airport() -> dict[str, pd.DataFrame]:
    def load():
        df = pd.read_csv(REF / "runways.csv", dtype={"runway": str})
        df = df.dropna(subset=["heading_true"])
        return {k: g for k, g in df.groupby("airport")}
    return _cached("runways", load)


def _metar_table() -> dict:
    """{(airport, local_hour_key): last observation row (dict)}."""
    def load():
        df = pd.read_csv(REF / "metar.csv", dtype={"local_hour_key": str, "wxcodes": str})
        df = df.sort_values("valid_utc").drop_duplicates(["airport", "local_hour_key"], keep="last")
        return {(r["airport"], r["local_hour_key"]): r for r in df.to_dict("records")}
    return _cached("metar", load)


def _aircraft_table() -> dict:
    def load():
        df = pd.read_csv(REF / "aircraft.csv", dtype=str)
        return {r["tail"]: r for r in df.to_dict("records")}
    return _cached("aircraft", load)


def _gates_table() -> dict[str, list[dict]]:
    def load():
        p = REF / "gates.csv"
        if not p.exists():
            return {}
        df = pd.read_csv(p, dtype=str)
        out: dict[str, list[dict]] = {}
        for r in df.to_dict("records"):
            out.setdefault(r["airport"], []).append(r)
        return out
    return _cached("gates", load)


def _airlines() -> dict[str, str]:
    def load():
        df = pd.read_csv(CH2 / "airlines.csv")
        return dict(zip(df["IATA_CODE"], df["AIRLINE"]))
    return _cached("airlines", load)


def _airports() -> dict[str, dict]:
    def load():
        df = pd.read_csv(CH2 / "airports.csv")
        return {r["IATA_CODE"]: r for r in df.to_dict("records")}
    return _cached("airports", load)


# ---------------------------------------------------------------- helpers

def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("".join(parts).encode()).hexdigest(), 16))


def _num(v, default=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _hhmm_min(hhmm) -> int:
    """HHMM -> minutes since local midnight (2400 -> 1439, kept inside the day)."""
    v = int(hhmm)
    return min(v // 100, 23) * 60 + (59 if v >= 2400 else v % 100)


def _parse_id(wave_id: str) -> tuple[str, str, int]:
    try:
        iata, y, m, d, h = wave_id.split("-")
        return iata, f"{y}-{m}-{d}", int(h)
    except ValueError:
        raise KeyError(wave_id) from None


def _index_row(wave_id: str) -> dict:
    idx = _index()
    hit = idx[idx["wave_id"] == wave_id]
    if hit.empty:
        raise KeyError(wave_id)
    return hit.iloc[0].to_dict()


def _haversine_nm(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def _airport_coords(iata: str) -> tuple[float, float] | None:
    a = _airports().get(iata)
    if a is not None:
        lat, lon = _num(a.get("LATITUDE")), _num(a.get("LONGITUDE"))
        if lat is not None and lon is not None:
            return lat, lon
    rw = _runways_by_airport().get(iata)
    if rw is not None:
        lat, lon = rw["le_lat"].dropna(), rw["le_lon"].dropna()
        if len(lat) and len(lon):
            return float(lat.mean()), float(lon.mean())
    return None


def _alternates() -> dict[str, tuple[float, float]]:
    def load():
        out = {}
        for iata in _runways_by_airport():
            c = _airport_coords(iata)
            if c is not None:
                out[iata] = c
        return out
    return _cached("alternates", load)


def _nearest_alternate(iata: str) -> tuple[str, float]:
    here = _airport_coords(iata)
    best = ("", float("inf"))
    if here is None:
        return "N/A", 0.0
    for other, (lat, lon) in _alternates().items():
        if other == iata:
            continue
        d = _haversine_nm(here[0], here[1], lat, lon)
        if d < best[1] and d > 5:
            best = (other, d)
    return best


# ---------------------------------------------------------------- window selection

def _window_rows(iata: str, date: str, hour: int) -> pd.DataFrame:
    """Non-cancelled arrivals at `iata` scheduled inside [hour:00, hour+1:00) local,
    one row per callsign, with `callsign`, `sched_min` (minutes vs T0) columns."""
    df, groups = _arrivals()
    pos = groups.get((iata, date))
    if pos is None:
        return df.iloc[0:0].assign(callsign=[], sched_min=[])
    w = df.iloc[pos]
    w = w[~w["cancelled"]]
    mins = w["sched_arr"].map(_hhmm_min)
    w = w[(mins >= hour * 60) & (mins < hour * 60 + 60)].copy()
    w["sched_min"] = (mins[w.index] - hour * 60).astype(float)
    w["callsign"] = w["carrier"] + w["flight_no"].astype(int).astype(str)
    return w.sort_values("sched_min").drop_duplicates("callsign")


def _select(w: pd.DataFrame, disrupted_cs: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split window rows into (negotiated, queue). Disrupted flights first (max 5, largest
    weather+late delay); pad with the nearest-by-sched_arr non-disrupted flights to 4
    (spec asks 3-5; 4 gives the negotiation something to trade) when available."""
    if w.empty:
        return w, w
    dis = w[w["callsign"].isin(disrupted_cs)].copy()
    if len(dis) > 5:
        dis = dis.assign(_s=dis["weather_delay"] + dis["late_aircraft_delay"]) \
                 .sort_values("_s", ascending=False, kind="stable").head(5).drop(columns="_s")
    chosen = list(dis["callsign"])
    others = w[~w["callsign"].isin(chosen)]
    centre = dis["sched_min"].median() if len(dis) else 30.0
    target = max(len(chosen), min(4, len(w)))
    pad = others.assign(_d=(others["sched_min"] - centre).abs()).sort_values("_d", kind="stable")
    chosen += list(pad["callsign"].head(max(0, target - len(chosen))))
    neg = w[w["callsign"].isin(chosen)]
    return neg, w[~w["callsign"].isin(chosen)]


# ---------------------------------------------------------------- weather

def _metar_for(iata: str, date: str, hour: int) -> Metar | None:
    tbl = _metar_table()
    d = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=hour)
    for dt in (d, d - timedelta(hours=1)):
        r = tbl.get((iata, dt.strftime("%Y-%m-%d-%H")))
        if r is not None:
            break
    else:
        return None
    drct = _num(r.get("drct"))
    codes = str(r["wxcodes"]).split() if isinstance(r.get("wxcodes"), str) else []
    gust = _num(r.get("gust"))
    valid = str(r["valid_utc"]).replace(" ", "T")
    return Metar(
        raw=str(r.get("raw", "")), station=str(r.get("station", iata)),
        observed_at=f"{valid}:00Z" if len(valid) == 16 else f"{valid}Z",
        wind_dir_deg=drct, wind_kt=_num(r.get("sknt"), 0.0) or 0.0, gust_kt=gust,
        visibility_sm=_num(r.get("vsby")), ceiling_ft=_num(r.get("ceiling_ft")),
        wx_codes=codes, surface=wind.surface_from_metar(codes))


def _weather_summary(m: Metar | None) -> str:
    if m is None:
        return "no METAR"
    d = "VRB" if m.wind_dir_deg is None else f"{int(round(m.wind_dir_deg)) % 360:03d}"
    g = f"G{int(round(m.gust_kt)):02d}" if m.gust_kt else ""
    parts = [f"{d}{int(round(m.wind_kt)):02d}{g}KT"]
    if m.visibility_sm is not None:
        parts.append(f"{m.visibility_sm:g}SM")
    parts += m.wx_codes
    return " ".join(parts) + f", {m.surface}"


# ---------------------------------------------------------------- runways / gates

def _runways(iata: str) -> list[Runway]:
    df = _runways_by_airport().get(iata)
    out = []
    if df is None:
        return out
    seen = set()
    for r in df.to_dict("records"):
        rid = str(r["runway"]).strip()
        if rid in seen:
            continue
        seen.add(rid)
        out.append(Runway(id=rid, heading_true=float(r["heading_true"]),
                          length_ft=int(_num(r.get("length_ft"), 0) or 0),
                          le_lat=_num(r.get("le_lat")), le_lon=_num(r.get("le_lon")),
                          he_lat=_num(r.get("he_lat")), he_lon=_num(r.get("he_lon"))))
    return out


def _close_unsafe(runways: list[Runway], rwinds: list) -> None:
    """Close runways outside FAA 8400.9 limits for a LARGE jet (aircraft_type=None)."""
    by_id = {r.id: r for r in runways}
    for rw in rwinds:
        if rw.within_limits:
            continue
        r = by_id[rw.runway]
        r.open = False
        if rw.crosswind_kt > rw.limit_crosswind_kt:
            r.closed_reason = (f"crosswind {rw.crosswind_kt:g} kt > {rw.limit_crosswind_kt:g} kt "
                               f"({rw.limit_source})")
        else:
            r.closed_reason = (f"tailwind {rw.tailwind_kt:g} kt > {rw.limit_tailwind_kt:g} kt "
                               f"({rw.limit_source})")


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "wide")


def _make_gates(wave_id: str, iata: str) -> tuple[list[Gate], bool]:
    real = _gates_table().get(iata)
    gates: list[Gate] = []
    if real:
        narrow = [r for r in real if not _truthy(r.get("wide"))][:10]
        wide = [r for r in real if _truthy(r.get("wide"))][-2:]
        for r, size in [(r, "narrow") for r in narrow] + [(r, "wide") for r in wide]:
            ref = str(r["gate_ref"]).strip()
            pier = "".join(itertools_takewhile_alpha(ref)).upper() or "T"
            gates.append(Gate(ref, size, pier))
    is_real = bool(gates)
    if gates and not any(g.size == "wide" for g in gates):
        # the OSM table has no wide-body gate for this airport: without one every wide-body
        # (B772, A359...) fails GND-GATE-SIZE by construction. Add two simulated wide gates.
        gates += [Gate("W1", "wide", "W"), Gate("W2", "wide", "W")]
    if not gates:
        for pier in "AB":
            for n in range(1, 6):
                gates.append(Gate(f"{pier}{n}", "wide" if n == 5 else "narrow", pier))
    for g in gates:
        rng = _rng(wave_id, g.id)
        if rng.random() < 0.30:
            dur = rng.randint(20, 60)
            start = rng.randint(-20, 60 - dur // 2)
            g.busy.append({"from_min": start, "to_min": start + dur, "label": "other-flight (simulated)"})
    gates.append(Gate("RS", "wide", "R", remote=True))
    return gates, is_real


def itertools_takewhile_alpha(s: str):
    for ch in s:
        if not ch.isalpha():
            break
        yield ch


def _gate_free(g: Gate, t: float) -> bool:
    return all(not (b["from_min"] <= t <= b["to_min"]) for b in g.busy)


# ---------------------------------------------------------------- flights

def _build_flight(wave_id: str, iata: str, date: str, hour: int, r: dict, disrupted: bool,
                  gates: list[Gate], used: set[str], origin_known: bool = True) -> WaveFlight:
    cs = r["callsign"]
    rng = _rng(wave_id, cs)
    tail = r.get("tail") if isinstance(r.get("tail"), str) else None
    ac = _aircraft_table().get(tail) if tail else None
    prov = [Provenance("flights", "real", f"BTS On-Time {date[:7]}",
                       "airborne inferred from scheduled departure vs window start; origin/"
                       "destination time zones ignored (BTS times are local)")]
    if ac:
        atype, wake, size = ac.get("type_icao") or None, ac.get("wake") or "LARGE", ac.get("gate_size") or "narrow"
        prov.append(Provenance("aircraft", "real", "FAA registry", ac.get("mfr_model")))
    else:
        atype, wake, size = None, "LARGE", "narrow"
        prov.append(Provenance("aircraft", "generic", "assumed narrow-body", "tail not in FAA registry"))

    sched_min = float(r["sched_min"])
    late, carr = float(r["late_aircraft_delay"]), float(r["carrier_delay"])
    # Earliest feasible arrival: a late inbound aircraft / carrier problem means it cannot
    # arrive earlier than sched + late_aircraft + carrier delay. Weather delay is deliberately
    # excluded: that is what the negotiation must absorb (holding, sequencing, diversion).
    eta_earliest = sched_min + late + carr

    arr_abs = _hhmm_min(r["sched_arr"])
    dep_hhmm = _num(r.get("sched_dep"))
    airborne = False
    if dep_hhmm is not None:
        dep_abs = _hhmm_min(dep_hhmm)
        dep_min = dep_abs - hour * 60 - (1440 if dep_abs > arr_abs else 0)
        airborne = dep_min < -30

    # gate: seeded pick of a size-compatible, preferably free and unused gate
    cands = [g for g in gates if not g.remote and g.size == size] or [g for g in gates if not g.remote]
    pref = [g for g in cands if g.id not in used and _gate_free(g, sched_min)] \
        or [g for g in cands if g.id not in used] or cands
    gate = pref[rng.randrange(len(pref))].id
    used.add(gate)

    alt, dist = _nearest_alternate(iata)
    fuel = {"extra_min": rng.randint(14, 45), "alternate": alt,
            "alternate_min": int(dist / 7 + 10), "final_reserve_min": 30}
    conns = [{"flight": f"{r['carrier']}{rng.randint(100, 2999)}", "pax": rng.randint(5, 40),
              "departs_min": sched_min + rng.randint(40, 120)} for _ in range(rng.randint(0, 3))]
    prov += [Provenance("fuel", "simulated", "seeded model", f"seed={wave_id}+{cs}"),
             Provenance("crew", "simulated", "seeded model",
                        "shift end = earliest turnaround end (ETA incl. late-aircraft/carrier delay "
                        "+ taxi + turnaround) + 0-90 min margin: the roster already knows the inbound is late"),
             Provenance("connections", "simulated", "seeded model")]
    # Crew shift end is anchored to the earliest achievable turnaround end, not the schedule: with
    # multi-hour BTS late-aircraft delays a schedule-anchored shift made ~70% of flights breach
    # GND-CREW-DUTY before any move (no move can land a flight earlier than its ETA). Airlines
    # re-roster / reserve-crew a known-late inbound; the 0-90 min margin keeps holding, far taxi and
    # ground delay able to cause overtime. One randint at the same position keeps later draws stable.
    crew_end = eta_earliest + TYPICAL_TAXI_MIN + TURNAROUND_MIN + rng.randint(0, 90)
    return WaveFlight(
        callsign=cs, carrier=r["carrier"], airline=_airlines().get(r["carrier"], r["carrier"]),
        flight_no=int(r["flight_no"]), tail=tail, aircraft_type=atype, wake=wake, gate_size=size,
        origin=str(r["origin"]), sched_arr=f"{date}T{hour:02d}:{int(sched_min):02d}:00",
        sched_min=sched_min, eta_earliest_min=eta_earliest, airborne=airborne, disrupted=disrupted,
        delays={"weather": float(r["weather_delay"]), "late_aircraft": late, "nas": float(r["nas_delay"]),
                "carrier": carr, "arr_delay": float(r["arr_delay"])},
        fuel=fuel, connections=conns, scheduled_gate=gate,
        crew_shift_end_min=crew_end,
        next_departure_min=sched_min + rng.randint(45, 120), provenance=prov)


def _queue(w: pd.DataFrame, runways: list[Runway], rwinds: list) -> list[QueueArrival]:
    open_rw = [r for r in runways if r.open]
    if not open_rw:
        return []
    hw = {x.runway: x.headwind_kt - x.crosswind_kt for x in rwinds}
    best = sorted(open_rw, key=lambda r: -hw.get(r.id, 0))[:2]
    out = []
    for i, r in enumerate(w.to_dict("records")):
        ac = _aircraft_table().get(r["tail"]) if isinstance(r.get("tail"), str) else None
        out.append(QueueArrival(
            callsign=r["callsign"], aircraft_type=(ac or {}).get("type_icao") or None,
            wake=(ac or {}).get("wake") or "LARGE", runway=best[i % len(best)].id,
            slot_min=float(r["sched_min"])))
    return out


# ---------------------------------------------------------------- public API

def build_wave(wave_id: str) -> Wave:
    iata, date, hour = _parse_id(wave_id)
    row = _index_row(wave_id)
    w = _window_rows(iata, date, hour)
    dis_cs = {c for c in str(row["disrupted_flights"]).split(";") if c}
    neg, rest = _select(w, dis_cs)

    runways = _runways(iata)
    metar = _metar_for(iata, date, hour)
    _close_unsafe(runways, wind.runway_wind(metar, runways))
    rwinds = wind.runway_wind(metar, runways)

    gates, real_gates = _make_gates(wave_id, iata)
    used: set[str] = set()
    flights = [_build_flight(wave_id, iata, date, hour, r, r["callsign"] in dis_cs, gates, used)
               for r in neg.to_dict("records")]

    ap = _airports().get(iata, {})
    coords = _airport_coords(iata) or (0.0, 0.0)
    airport = Airport(iata=iata, icao="K" + iata, name=str(ap.get("AIRPORT", iata)),
                      lat=coords[0], lon=coords[1], runways=runways)
    month = date[:7]
    prov = [
        Provenance("queue", "real", f"BTS On-Time {month}"),
        Provenance("runways", "real", "OurAirports"),
        (Provenance("weather", "real", "IEM METAR", metar.raw) if metar
         else Provenance("weather", "generic", "no METAR found for window")),
        (Provenance("gates", "real", "OpenStreetMap",
                    "W1, W2 simulated wide-body gates (none in OSM data)"
                    if any(g.id in ("W1", "W2") for g in gates) else None) if real_gates
         else Provenance("gates", "generic", "generic layout A1-A5, B1-B5")),
        Provenance("gate_occupancy", "simulated", "seeded model", f"seed={wave_id}"),
    ]
    return Wave(id=wave_id, airport=airport, window_start=f"{date}T{hour:02d}:00:00",
                metar=metar, runway_wind=rwinds, queue=_queue(rest, runways, rwinds),
                flights=flights, gates=gates, provenance=prov)


def _summary(row: dict) -> dict:
    wid = row["wave_id"]
    iata, date, hour = _parse_id(wid)
    w = _window_rows(iata, date, hour)
    dis_cs = {c for c in str(row["disrupted_flights"]).split(";") if c}
    neg, rest = _select(w, dis_cs)
    start = datetime.strptime(f"{date} {hour:02d}", "%Y-%m-%d %H")
    ap = _airports().get(iata, {})
    return {
        "id": wid,
        "airport": {"iata": iata, "icao": "K" + iata, "name": str(ap.get("AIRPORT", iata))},
        "window_start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        "n_flights": len(neg), "n_queue": len(rest),
        "callsigns": list(neg["callsign"]),
        "disrupted_callsigns": [c for c in neg["callsign"] if c in dis_cs],
        "weather_summary": _weather_summary(_metar_for(iata, date, hour)),
        "replay_available": (REPLAY / f"{wid}.json").exists(),
    }


def list_waves(airport: str | None = None, q: str | None = None, limit: int = 50) -> list[dict]:
    idx = _index()
    if airport:
        idx = idx[idx["airport"] == airport.upper()]
    if q:
        qq = q.strip().upper()
        idx = idx[idx["wave_id"].str.upper().str.contains(qq, regex=False)
                  | idx["disrupted_flights"].str.upper().str.contains(qq, regex=False)]
    return [_summary(r) for r in idx.head(limit).to_dict("records")]


def random_wave(airport: str | None = None, seed: int | None = None) -> dict:
    idx = _index()
    if airport:
        idx = idx[idx["airport"] == airport.upper()]
    if idx.empty:
        raise KeyError(airport)
    rng = random.Random(seed)
    return _summary(idx.iloc[rng.randrange(len(idx))].to_dict())
