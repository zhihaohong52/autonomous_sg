"""Regenerate frontend/src/api/samples/* from the real API (FastAPI TestClient over atm.server.app).

Usage: PYTHONPATH=. python3 scripts/make_samples.py [wave_id]
Uses a pre-recorded run (replay) so no model is called; runs and decisions are written to temp dirs."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from atm import server

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "samples"
WAVE_ID = sys.argv[1] if len(sys.argv) > 1 else "F2015-270287"


def _write_json(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n")
    print("wrote", name)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, \
            mock.patch.object(server, "RUNS_DIR", Path(tmp) / "runs"), \
            mock.patch.object(server, "DECISIONS", Path(tmp) / "decisions.jsonl"), \
            mock.patch.object(server, "REPLAY_SPACING_S", 0):
        c = TestClient(server.app)

        def get(path: str):
            r = c.get(path)
            r.raise_for_status()
            return r.json()

        _write_json("config.json", get("/api/config"))
        _write_json("flights.json", get("/api/flights?airport=ATL&limit=3"))
        _write_json("waves.json", get("/api/waves?limit=3"))
        _write_json("wave.json", get(f"/api/wave/{WAVE_ID}"))

        r = c.post("/api/negotiate-wave", json={"wave_id": WAVE_ID, "mode": "decision", "replay": True})
        r.raise_for_status()
        (OUT / "negotiate_stream.txt").write_text(r.text)
        print("wrote negotiate_stream.txt")
        run_id = next(json.loads(b.split("data: ", 1)[1]) for b in r.text.split("\n\n")
                      if b.startswith("event: start"))["run_id"]
        _write_json("result.json", get(f"/api/runs/{run_id}"))

        d = c.post("/api/decision", json={"run_id": run_id, "decision": "APPROVE"})
        d.raise_for_status()
        _write_json("decision.json", d.json())

        if server.BENCHMARK.exists():
            _write_json("benchmark.json", get("/api/benchmark"))


if __name__ == "__main__":
    main()
