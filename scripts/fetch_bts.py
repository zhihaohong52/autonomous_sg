"""Fetch BTS On-Time PREZIP months and build wave arrivals + index.

Usage: python3 scripts/fetch_bts.py
Writes data/waves/arrivals.csv.gz and data/waves/index.csv.
"""
import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atm.wave.hubs import HUBS, MONTHS  # noqa: E402

CACHE = ROOT / ".cache" / "bts"
OUT = ROOT / "data" / "waves"
URL = ("https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_"
       "Performance_1987_present_{y}_{m}.zip")

COLS = {
    "FlightDate": "date", "Reporting_Airline": "carrier",
    "Flight_Number_Reporting_Airline": "flight_no", "Tail_Number": "tail",
    "Origin": "origin", "Dest": "dest", "CRSDepTime": "sched_dep",
    "CRSArrTime": "sched_arr", "DepTime": "dep_time", "ArrTime": "arr_time",
    "ArrDelay": "arr_delay", "WeatherDelay": "weather_delay",
    "NASDelay": "nas_delay", "LateAircraftDelay": "late_aircraft_delay",
    "CarrierDelay": "carrier_delay", "Distance": "distance",
    "Cancelled": "cancelled", "Diverted": "diverted",
}
DELAYS = ["arr_delay", "weather_delay", "nas_delay", "late_aircraft_delay",
          "carrier_delay"]


def download(month: str) -> Path:
    y, m = month.split("-")
    dest = CACHE / f"{y}_{int(m)}" / "bts.zip"
    if dest.exists() and zipfile.is_zipfile(dest):  # partial zips lack the footer
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = URL.format(y=y, m=int(m))
    subprocess.run(["curl", "-sSL", "--fail", "-o", str(dest), url], check=True)
    return dest


def load_month(zpath: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        with z.open(names[0]) as f:  # read only; nothing is extracted/executed
            df = pd.read_csv(f, usecols=list(COLS), low_memory=False)
    df = df.rename(columns=COLS)
    return df[df["dest"].isin(HUBS)]


def build_index(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["cancelled"] == 0].copy()
    d["hour"] = (d["sched_arr"] // 100) % 24
    d["disrupted"] = (d["weather_delay"] > 15) | (d["late_aircraft_delay"] > 20)
    d["fid"] = d["carrier"].astype(str) + d["flight_no"].astype(str)
    rows = []
    for (ap, date, hour), g in d.groupby(["dest", "date", "hour"]):
        n = len(g)
        dis = g[g["disrupted"]]
        if n < 3 or dis.empty:
            continue
        rows.append({
            "wave_id": f"{ap}-{date}-{hour:02d}", "airport": ap, "date": date,
            "hour": hour, "n_arrivals": n, "n_disrupted": len(dis),
            "disrupted_flights": ";".join(dis["fid"]),
        })
    idx = pd.DataFrame(rows)
    return idx.sort_values(["n_disrupted", "wave_id"],
                           ascending=[False, True]).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", default=MONTHS,
                    help="YYYY-MM months to include (default: hubs.MONTHS)")
    months = ap.parse_args().months
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [load_month(download(m)) for m in months]
    df = pd.concat(frames, ignore_index=True)
    df[DELAYS] = df[DELAYS].fillna(0)
    for c in ("sched_dep", "sched_arr"):
        df[c] = df[c].astype(int)
    df["cancelled"] = df["cancelled"].astype(int)
    df["diverted"] = df["diverted"].astype(int)
    df = df[list(COLS.values())]
    df.to_csv(OUT / "arrivals.csv.gz", index=False, compression={"method": "gzip", "compresslevel": 9})
    idx = build_index(df)
    idx.to_csv(OUT / "index.csv", index=False)
    print(f"arrivals={len(df)} waves={len(idx)}")
    print(idx["airport"].value_counts().head(5).to_string())


if __name__ == "__main__":
    main()
