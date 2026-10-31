"""Extract disrupted flights (brief: WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20) into a small CSV.

Accepts either the 2015 DOT flights.csv (Kaggle usdot/flight-delays) or the BTS 2023-2025
file shipped with Challenge 5. Usage: python scripts/extract_disruptions.py <input.csv> [out.csv]
"""
import sys

import pandas as pd

SCHEMA_2015 = {
    "YEAR": "year", "MONTH": "month", "DAY": "day", "AIRLINE": "carrier",
    "FLIGHT_NUMBER": "flight_no", "TAIL_NUMBER": "tail", "ORIGIN_AIRPORT": "origin",
    "DESTINATION_AIRPORT": "dest", "SCHEDULED_DEPARTURE": "sched_dep",
    "SCHEDULED_ARRIVAL": "sched_arr", "DISTANCE": "distance", "ARRIVAL_DELAY": "arr_delay",
    "WEATHER_DELAY": "weather_delay", "LATE_AIRCRAFT_DELAY": "late_aircraft_delay",
    "AIR_SYSTEM_DELAY": "nas_delay",
}
SCHEMA_BTS = {
    "YEAR": "year", "MONTH": "month", "DAY_OF_MONTH": "day", "OP_UNIQUE_CARRIER": "carrier",
    "OP_CARRIER_FL_NUM": "flight_no", "TAIL_NUM": "tail", "ORIGIN": "origin", "DEST": "dest",
    "CRS_DEP_TIME": "sched_dep", "CRS_ARR_TIME": "sched_arr", "DISTANCE": "distance",
    "ARR_DELAY": "arr_delay", "WEATHER_DELAY": "weather_delay",
    "LATE_AIRCRAFT_DELAY": "late_aircraft_delay", "NAS_DELAY": "nas_delay",
    "wspd_DEST": "dest_wind_kmh", "wpgt_DEST": "dest_gust_kmh", "prcp_DEST": "dest_precip_mm",
    "coco_DEST": "dest_condition",
}


def main(src: str, out: str) -> None:
    head = pd.read_csv(src, nrows=0).columns
    schema = SCHEMA_2015 if "DESTINATION_AIRPORT" in head else SCHEMA_BTS
    df = pd.read_csv(src, usecols=[c for c in schema if c in head], low_memory=False).rename(columns=schema)
    df = df[(df.weather_delay > 15) | (df.late_aircraft_delay > 20)].copy()
    df = df.dropna(subset=["sched_arr", "distance"])
    df["flight_no"] = df.flight_no.astype(int)
    df.insert(0, "id", range(len(df)))
    df.to_csv(out, index=False)
    print(f"{len(df)} disrupted flights -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/challenge2/disruptions.csv")
