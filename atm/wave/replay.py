"""Record and load pre-recorded negotiation runs (data/replay/{wave_id}.json) for no-model-call replay."""
from __future__ import annotations

import json
from pathlib import Path

from atm.wave.loader import _load

WB = _load("build")
GR = _load("graph")

REPLAY_DIR = Path(__file__).resolve().parents[2] / "data" / "replay"


def record(wave_ids: list[str], mode: str = "decision", out_dir: str | Path = REPLAY_DIR) -> list[str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for wid in wave_ids:
        msgs: list[dict] = []
        res = GR.negotiate_wave(WB.build_wave(wid), mode, chain=["formula"] if mode == "formulas" else None,
                                on_message=msgs.append)
        res = {k: v for k, v in res.items() if k != "log"}
        p = out / f"{wid}.json"
        p.write_text(json.dumps({"messages": msgs, "result": res}))
        paths.append(str(p))
    return paths


def load(wave_id: str, out_dir: str | Path = REPLAY_DIR) -> dict:
    """Raises FileNotFoundError if the wave has no recording."""
    return json.loads((Path(out_dir) / f"{wave_id}.json").read_text())
