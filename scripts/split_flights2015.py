"""Split data/challenge2/flights.csv (358,774 rows, 2015 US DOT flights) into one small file per destination,
data/challenge2/flights/{DEST}.csv.gz, plus a light index data/challenge2/flights_index.csv. The server
never loads the full file (512 MB host); row_id is the 1-based data-row number in flights.csv.

    PYTHONPATH=. python3 scripts/split_flights2015.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CH2 = Path(__file__).resolve().parent.parent / "data" / "challenge2"
SRC = {"AIRLINE": "carrier", "FLIGHT_NUMBER": "flight_no", "TAIL_NUMBER": "tail", "ORIGIN_AIRPORT": "origin",
       "DESTINATION_AIRPORT": "dest", "SCHEDULED_DEPARTURE": "sched_dep", "SCHEDULED_ARRIVAL": "sched_arr",
       "ARRIVAL_DELAY": "arr_delay", "WEATHER_DELAY": "weather_delay", "LATE_AIRCRAFT_DELAY": "late_aircraft_delay",
       "AIR_SYSTEM_DELAY": "nas_delay", "AIRLINE_DELAY": "carrier_delay", "DIVERTED": "diverted",
       "CANCELLED": "cancelled"}
DELAYS = ["arr_delay", "weather_delay", "late_aircraft_delay", "nas_delay", "carrier_delay"]
FILE_COLS = ["row_id", "date", "arr_date", "carrier", "flight_no", "tail", "origin", "sched_dep", "sched_arr",
             "arr_delay", "weather_delay", "late_aircraft_delay", "nas_delay", "carrier_delay", "diverted",
             "cancelled"]
INDEX_COLS = ["row_id", "date", "carrier", "flight_no", "tail", "origin", "dest", "sched_arr", "arr_delay",
              "weather_delay", "late_aircraft_delay", "diverted", "cancelled", "arr_date"]


def _min(hhmm: pd.Series) -> pd.Series:
    v = hhmm.astype(int)
    return (v // 100).clip(upper=23) * 60 + v % 100


def main() -> None:
    raw = pd.read_csv(CH2 / "flights.csv", dtype={"TAIL_NUMBER": str, "ORIGIN_AIRPORT": str,
                                                  "DESTINATION_AIRPORT": str})
    df = raw[["YEAR", "MONTH", "DAY"] + list(SRC)].rename(columns=SRC)
    df.insert(0, "row_id", range(1, len(df) + 1))
    df = df.dropna(subset=["sched_arr", "sched_dep", "carrier", "flight_no"]).copy()
    df = df[df["dest"].str.fullmatch(r"[A-Z]{3}") & df["origin"].str.fullmatch(r"[A-Z]{3}")]
    date = pd.to_datetime(dict(year=df["YEAR"], month=df["MONTH"], day=df["DAY"]))
    df["date"] = date.dt.strftime("%Y-%m-%d")
    # BTS dates are the departure date; a scheduled arrival clock before the departure clock means next day
    nxt = _min(df["sched_arr"]) < _min(df["sched_dep"])
    df["arr_date"] = (date + pd.to_timedelta(nxt.astype(int), unit="D")).dt.strftime("%Y-%m-%d")
    for c in DELAYS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    for c in ("flight_no", "sched_dep", "sched_arr", "diverted", "cancelled"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    out = CH2 / "flights"
    out.mkdir(exist_ok=True)
    n = 0
    for dest, g in df.groupby("dest", sort=True):
        g[FILE_COLS].to_csv(out / f"{dest}.csv.gz", index=False)
        n += 1
    df[INDEX_COLS].to_csv(CH2 / "flights_index.csv", index=False)
    print(f"wrote {n} files to {out} and flights_index.csv ({len(df)} of {len(raw)} rows)")


if __name__ == "__main__":
    main()
