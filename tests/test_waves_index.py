import re
from pathlib import Path

import pandas as pd

from atm.wave.hubs import HUBS

INDEX = Path(__file__).resolve().parent.parent / "data" / "waves" / "index.csv"


def test_index_definition():
    idx = pd.read_csv(INDEX, dtype={"date": str})
    assert len(idx) >= 500
    assert (idx["n_disrupted"] >= 1).all()
    assert (idx["n_arrivals"] >= 3).all()
    assert idx["airport"].isin(HUBS).all()
    pat = re.compile(r"^[A-Z]{3}-\d{4}-\d{2}-\d{2}-\d{2}$")
    for r in idx.itertuples():
        assert pat.match(r.wave_id)
        assert r.wave_id == f"{r.airport}-{r.date}-{r.hour:02d}"
