"""Split data/waves/arrivals.csv.gz (scripts/fetch_bts.py output, ~1.2M rows) into one small file per
destination hub: data/waves/arrivals/{IATA}.csv.gz. atm/wave/build.py loads only the airport it needs, so
the server fits in 512 MB. Applies the same cleaning build.py always did (drop rows without
sched_arr/carrier/flight_no, delays -> 0, cancelled -> bool) so wave output is unchanged.

    PYTHONPATH=. python3 scripts/split_arrivals.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

WAVES = Path(__file__).resolve().parent.parent / "data" / "waves"
KEEP = ["date", "carrier", "flight_no", "tail", "origin", "sched_dep", "sched_arr", "weather_delay",
        "nas_delay", "late_aircraft_delay", "carrier_delay", "arr_delay", "cancelled"]
DELAYS = ["weather_delay", "nas_delay", "late_aircraft_delay", "carrier_delay", "arr_delay"]


def main() -> None:
    df = pd.read_csv(WAVES / "arrivals.csv.gz", dtype={"date": str, "tail": str, "carrier": str})
    df = df.dropna(subset=["sched_arr", "carrier", "flight_no"]).reset_index(drop=True)
    for c in DELAYS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["cancelled"] = (pd.to_numeric(df["cancelled"], errors="coerce").fillna(0) > 0).astype(int)
    df["flight_no"] = df["flight_no"].astype(int)
    df["sched_arr"] = df["sched_arr"].astype(int)
    out = WAVES / "arrivals"
    out.mkdir(exist_ok=True)
    n = 0
    for dest, g in df.groupby("dest", sort=True):
        g[KEEP].to_csv(out / f"{dest}.csv.gz", index=False)
        n += 1
    print(f"wrote {n} files to {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
