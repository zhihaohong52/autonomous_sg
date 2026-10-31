"""Fetch reference data into data/ref/ (runways, metar, aircraft, gates).

Usage: python3 scripts/fetch_ref.py [runways|metar|aircraft|gates]   (default: all)
Downloads go to .cache/ref/ and are only parsed, never executed.
"""
import csv
import io
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atm.wave.hubs import HUBS, MONTHS  # noqa: E402,F401

OUT = ROOT / "data" / "ref"
CACHE = ROOT / ".cache" / "ref"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

TZ = {
    "ATL": "America/New_York", "DFW": "America/Chicago", "DEN": "America/Denver",
    "ORD": "America/Chicago", "LAX": "America/Los_Angeles", "JFK": "America/New_York",
    "LAS": "America/Los_Angeles", "MCO": "America/New_York", "CLT": "America/New_York",
    "PHX": "America/Phoenix", "SEA": "America/Los_Angeles", "MIA": "America/New_York",
    "SFO": "America/Los_Angeles", "IAH": "America/Chicago", "EWR": "America/New_York",
    "BOS": "America/New_York", "MSP": "America/Chicago", "DTW": "America/New_York",
    "FLL": "America/New_York", "LGA": "America/New_York", "PHL": "America/New_York",
    "BWI": "America/New_York", "SLC": "America/Denver", "DCA": "America/New_York",
    "SAN": "America/Los_Angeles", "IAD": "America/New_York", "TPA": "America/New_York",
    "MDW": "America/Chicago", "BNA": "America/Chicago", "AUS": "America/Chicago",
}


def curl(url, dest, data=None, retries=2, ua=UA, max_time=300):
    """Download url to dest (a Path) via curl. Returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-sSL", "--fail", "-A", ua, "--max-time", str(max_time), "-o", str(dest)]
    if data is not None:
        cmd += ["--data-urlencode", f"data={data}"]
    cmd.append(url)
    for _ in range(retries + 1):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return True
        time.sleep(2)
    print(f"  curl failed {url[:80]}: {r.stderr.strip()[:200]}", file=sys.stderr)
    return False


def fresh_dir(name):
    d = CACHE / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_csv(path, encoding="utf-8-sig"):
    with open(path, newline="", encoding=encoding, errors="replace") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- runways
HARD = {"ASP", "CON", "ASPH", "CONC", "BIT", "ASPH-G", "CONC-G", "ASPH-CONC", "CONC-ASPH",
        "PEM", "PAVED", "TAR", "MAC", "BITUMINOUS"}


def _hard(surface):
    s = (surface or "").upper().strip()
    return bool(s) and any(k in s for k in ("ASP", "CON", "BIT", "PEM", "TAR", "MAC", "PAVED"))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def runways():
    d = fresh_dir("ourairports")
    rw, ap = d / "runways.csv", d / "airports.csv"
    base = "https://davidmegginson.github.io/ourairports-data/"
    if not (curl(base + "runways.csv", rw) and curl(base + "airports.csv", ap)):
        raise SystemExit("runways download failed")
    wanted = set(HUBS) | {r["IATA_CODE"] for r in read_csv(ROOT / "data/challenge2/airports.csv")}
    ident2iata = {}
    for r in read_csv(ap):
        if r["iata_code"] in wanted:
            ident2iata[r["ident"]] = r["iata_code"]
    rows = []
    for r in read_csv(rw):
        iata = ident2iata.get(r["airport_ident"])
        if not iata or r["closed"] != "0" or not _hard(r["surface"]):
            continue
        length = _f(r["length_ft"])
        if length is None or length < 5000:
            continue
        for a, b in (("le", "he"), ("he", "le")):
            ident = r[f"{a}_ident"]
            hdg = _f(r[f"{a}_heading_degT"])
            if hdg is None:
                m = re.match(r"\d+", ident or "")
                if not m:
                    continue
                hdg = int(m.group()) * 10.0
            lat, lon = _f(r[f"{a}_latitude_deg"]), _f(r[f"{a}_longitude_deg"])
            olat, olon = _f(r[f"{b}_latitude_deg"]), _f(r[f"{b}_longitude_deg"])
            if None in (lat, lon, olat, olon) or not ident:
                continue
            rows.append([iata, ident, round(hdg, 1), int(length), lat, lon, olat, olon, r["surface"]])
    rows.sort()
    _write("runways.csv", "airport runway heading_true length_ft le_lat le_lon he_lat he_lon surface".split(), rows)


def _write(name, header, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {name}: {len(rows)} rows")


# ------------------------------------------------------------------ metar
def _ceiling(row):
    best = None
    for i in (1, 2, 3):
        cov, lvl = row.get(f"skyc{i}", "M"), row.get(f"skyl{i}", "M")
        if cov in ("BKN", "OVC", "VV") and lvl not in ("M", ""):
            v = _f(lvl)
            if v is not None and (best is None or v < best):
                best = v
    return "" if best is None else int(best)


def _m(x):
    return "" if x in (None, "M") else x


def metar():
    d = fresh_dir("iem")
    rows = []
    for i, hub in enumerate(HUBS):
        if i:
            time.sleep(1)
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=" + hub +
               "&data=drct&data=sknt&data=gust&data=vsby&data=skyc1&data=skyl1&data=skyc2"
               "&data=skyl2&data=skyc3&data=skyl3&data=wxcodes&data=metar&year1=2024&month1=6"
               "&day1=1&year2=2024&month2=9&day2=1&tz=Etc/UTC&format=onlycomma&latlon=no"
               "&missing=M&trace=T&report_type=3")
        p = d / f"{hub}.csv"
        if not curl(url, p):
            print(f"  METAR skipped {hub}", file=sys.stderr)
            continue
        tz = ZoneInfo(TZ[hub])
        best = {}
        for r in read_csv(p):
            try:
                t = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            key = t.astimezone(tz).strftime("%Y-%m-%d-%H")
            if key not in best or t > best[key][0]:
                best[key] = (t, r)
        n = 0
        for key, (t, r) in sorted(best.items()):
            rows.append([r.get("station", hub), hub, t.strftime("%Y-%m-%d %H:%M"), key,
                         _m(r["drct"]), _m(r["sknt"]), _m(r["gust"]), _m(r["vsby"]),
                         _ceiling(r), _m(r["wxcodes"]), _m(r["metar"])])
            n += 1
        print(f"  {hub}: {n} hours")
    _write("metar.csv", "station airport valid_utc local_hour_key drct sknt gust vsby ceiling_ft wxcodes raw".split(), rows)


# --------------------------------------------------------------- aircraft
# (regex on normalised model string, type_icao, wake)
def classify(mfr, model):
    """Return (type_icao, wake, gate_size) or None."""
    m = re.sub(r"[\s_]+", "-", model.upper().strip())
    f = mfr.upper()
    wide = "wide"
    if f.startswith("AIRBUS"):
        if m.startswith("A318"): return "A318", "LARGE", "narrow"
        if m.startswith("A319"): return "A319", "LARGE", "narrow"
        if m.startswith("A320"): return ("A20N" if "-27" in m else "A320"), "LARGE", "narrow"
        if m.startswith("A321"): return "A321", "LARGE", "narrow"
        if m.startswith("A330-2"): return "A332", "HEAVY", wide
        if m.startswith("A330-3"): return "A333", "HEAVY", wide
        if m.startswith("A330-9"): return "A339", "HEAVY", wide
        if m.startswith("A350"): return "A359", "HEAVY", wide
        if m.startswith("A380"): return "A388", "SUPER", wide
        if m.startswith("A220-1") or m.startswith("BD-500-1A10"): return "BCS1", "LARGE", "narrow"
        if m.startswith("A220-3") or m.startswith("BD-500-1A11"): return "BCS3", "LARGE", "narrow"
        return None
    if "AIRBUS CANADA" in f or "BOMBARDIER" in f or "C SERIES" in f:
        if m.startswith("BD-500-1A10") or m.startswith("A220-1"): return "BCS1", "LARGE", "narrow"
        if m.startswith("BD-500-1A11") or m.startswith("A220-3"): return "BCS3", "LARGE", "narrow"
        if m.startswith("CL-600-2C10") or re.match(r"CRJ-?700", m): return "CRJ7", "LARGE", "narrow"
        if m.startswith("CL-600-2D24") or re.match(r"CRJ-?900", m): return "CRJ9", "LARGE", "narrow"
        return None
    if "CANADAIR" in f:
        if m.startswith("CL-600-2C10"): return "CRJ7", "LARGE", "narrow"
        if m.startswith("CL-600-2D24"): return "CRJ9", "LARGE", "narrow"
        return None
    if "EMBRAER" in f:
        if re.match(r"(ERJ-?)?170", m) or "EMB-170" in m or "ERJ-170" in m:
            return "E170", "LARGE", "narrow"
        if re.match(r"(ERJ-?)?175", m) or "EMB-175" in m: return "E175", "LARGE", "narrow"
        if re.match(r"(ERJ-?)?190", m) or "EMB-190" in m: return "E190", "LARGE", "narrow"
        if "145" in m: return "E145", "LARGE", "narrow"
        if "175" in m: return "E175", "LARGE", "narrow"
        if "170" in m: return "E170", "LARGE", "narrow"
        if "190" in m: return "E190", "LARGE", "narrow"
        return None
    if f.startswith("BOEING"):
        if m.startswith("737"):
            if m.startswith(("737-7", "737-6", "737-3", "737-4", "737-5", "737-2", "737-1")):
                return "B737", "LARGE", "narrow"
            if m.startswith("737-8"):
                return ("B38M" if m in ("737-8", "737-8-MAX", "737-MAX-8") or "MAX" in m else "B738"), "LARGE", "narrow"
            if m.startswith("737-9"):
                return ("B39M" if m in ("737-9", "737-9-MAX", "737-MAX-9") or "MAX" in m else "B739"), "LARGE", "narrow"
            if "MAX-8" in m: return "B38M", "LARGE", "narrow"
            if "MAX-9" in m: return "B39M", "LARGE", "narrow"
            return None
        if m.startswith("757-2"): return "B752", "B757", "narrow"
        if m.startswith("757-3"): return "B753", "B757", "narrow"
        if m.startswith("767-3"): return "B763", "HEAVY", wide
        if m.startswith("767-4"): return "B764", "HEAVY", wide
        if m.startswith("777-2"): return "B772", "HEAVY", wide
        if m.startswith("777-3"): return "B77W", "HEAVY", wide
        if m.startswith("787-8"): return "B788", "HEAVY", wide
        if m.startswith("787-9"): return "B789", "HEAVY", wide
        if m.startswith("787-10"): return "B78X", "HEAVY", wide
        return None
    return None


def aircraft():
    d = fresh_dir("faa")
    z = d / "ReleasableAircraft.zip"
    if not curl("https://registry.faa.gov/database/ReleasableAircraft.zip", z):
        raise SystemExit("FAA download failed")
    with zipfile.ZipFile(z) as zf:
        def rd(name):
            raw = zf.read(name).decode("utf-8-sig", errors="replace")
            rdr = csv.DictReader(io.StringIO(raw))
            rdr.fieldnames = [h.strip() for h in rdr.fieldnames]
            return [{k: (v or "").strip() for k, v in r.items() if k} for r in rdr]
        ref = {r["CODE"]: r for r in rd("ACFTREF.txt")}
        master = rd("MASTER.txt")
    rows, unmapped = [], {}
    for r in master:
        a = ref.get(r["MFR MDL CODE"])
        if not a:
            continue
        try:
            seats = int(a["NO-SEATS"])
        except ValueError:
            continue
        if seats < 50 or a["TYPE-ENG"] not in ("4", "5"):  # turbo-jet / turbo-fan
            continue
        c = classify(a["MFR"], a["MODEL"])
        if not c:
            unmapped[(a["MFR"], a["MODEL"])] = unmapped.get((a["MFR"], a["MODEL"]), 0) + 1
            continue
        rows.append(["N" + r["N-NUMBER"], c[0], f'{a["MFR"]} {a["MODEL"]}', c[1], c[2]])
    rows.sort()
    _write("aircraft.csv", "tail type_icao mfr_model wake gate_size".split(), rows)
    top = sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]
    print("  top unmapped jets>=50 seats:", top)


# ------------------------------------------------------------------ gates
def gates():
    d = fresh_dir("overpass")
    rows = []
    for hub in HUBS[:10]:
        q = (f'[out:json][timeout:80];area["aeroway"="aerodrome"]["iata"="{hub}"]->.a;'
             f'(node["aeroway"="gate"](area.a););out tags;')
        p = d / f"{hub}.json"
        ok = curl("https://overpass-api.de/api/interpreter", p, data=q, retries=1,
                  ua="disruption-negotiator-hackathon/0.1", max_time=100)
        time.sleep(2)
        if not ok:
            print(f"  gates skipped {hub}", file=sys.stderr)
            continue
        import json
        try:
            els = json.loads(p.read_text()).get("elements", [])
        except ValueError:
            print(f"  gates bad json {hub}", file=sys.stderr)
            continue
        seen = set()
        for e in els:
            t = e.get("tags", {})
            ref = t.get("ref") or t.get("name") or str(e.get("id"))
            if ref in seen:
                continue
            seen.add(ref)
            txt = " ".join(str(v) for v in t.values()).lower()
            wide = bool(re.search(r"wide|heavy|widebody|wide-body|\b(747|777|787|a330|a350|a380)\b", txt))
            rows.append([hub, ref, wide])
        print(f"  {hub}: {len(seen)} gates")
    _write("gates.csv", ["airport", "gate_ref", "wide"], rows)


STEPS = {"runways": runways, "metar": metar, "aircraft": aircraft, "gates": gates}

if __name__ == "__main__":
    which = sys.argv[1:] or list(STEPS)
    for w in which:
        print(f"== {w}")
        STEPS[w]()
