"""API tests for wave mode: stub-backed, no network."""
import json

import pytest
from fastapi.testclient import TestClient

from atm import server
from atm.wave import stub

WID = "ATL-2024-07-15-16"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WB", stub)
    monkeypatch.setattr(server, "GR", stub)
    monkeypatch.setattr(server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(server, "DECISIONS", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(server, "REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(server, "BENCHMARK", tmp_path / "benchmark.json")
    monkeypatch.setattr(stub, "_REPLAY_DIR", tmp_path / "replay")
    monkeypatch.setattr(server, "REPLAY_SPACING_S", 0)
    server._RATE.clear()
    server.RUNS.clear()
    return TestClient(server.app)


def _frames(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = block.split("\n", 1)
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_sse_frames_order(client):
    r = client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "decision"})
    assert r.headers["content-type"].startswith("text/event-stream")
    fr = _frames(r.text)
    assert fr[0][0] == "start" and fr[-1][0] == "result"
    assert fr[0][1]["run_id"] == fr[-1][1]["run_id"]
    msgs = [d for e, d in fr if e == "message"]
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs) and len(msgs) >= 8
    assert [m["type"] for m in msgs][:2] == ["TRIAGE", "OPEN"]
    assert client.get(f"/api/runs/{fr[-1][1]['run_id']}").json()["log"]


def test_stream_false_returns_log(client):
    r = client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "formulas", "stream": False})
    j = r.json()
    assert r.status_code == 200 and j["log"] and j["outcome"] == "consensus" and j["run_id"]
    assert j["baseline"]["plan"] and j["narration"] is None


def test_override_requires_note(client):
    r = client.post("/api/decision", json={"run_id": "abc", "decision": "OVERRIDE"})
    assert r.status_code == 422 and r.json()["error"]["code"]
    r = client.post("/api/decision", json={"run_id": "abc", "decision": "OVERRIDE", "note": "  "})
    assert r.status_code == 422
    r = client.post("/api/decision", json={"run_id": "abc", "decision": "APPROVE"})
    assert r.json()["ok"] is True and r.json()["recorded_at"]
    assert json.loads(server.DECISIONS.read_text().splitlines()[-1])["decision"] == "APPROVE"


def test_v1_decision_still_works(client):
    r = client.post("/api/decision", json={"flight_id": 1, "plan_id": "p", "decision": "APPROVE"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.post("/api/decision", json={"flight_id": 1}).status_code == 422


def test_rate_limit_429(client):
    body = {"wave_id": WID, "mode": "formulas", "stream": False}
    for _ in range(10):
        assert client.post("/api/negotiate-wave", json=body).status_code == 200
    r = client.post("/api/negotiate-wave", json=body)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited" and r.json()["retry_after_s"] > 0


def test_unknown_wave_404(client):
    assert client.get("/api/wave/NOPE").status_code == 404
    assert client.get("/api/wave/NOPE").json()["error"]["code"]
    assert client.post("/api/negotiate-wave", json={"wave_id": "NOPE", "mode": "decision"}).status_code == 404
    assert client.get("/api/runs/deadbeef0000").status_code == 404


def test_config_keys(client):
    j = client.get("/api/config").json()
    for k in ("version", "modes", "default_mode", "models", "default_model", "llm_available", "tracing",
              "replay_available", "jev_available", "model", "policies", "n_disruptions"):
        assert k in j, k
    assert j["modes"] == ["formulas", "decision", "decision+claude"]
    assert {"id": "formula", "provider": "formula"}.items() <= j["models"][-1].items()
    assert j["replay_available"] is False


def test_replay_missing_404(client):
    r = client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "decision", "replay": True})
    assert r.status_code == 404 and r.json()["error"]["code"]


def test_replay_streams_saved_run(client, monkeypatch):
    from atm.wave import replay
    monkeypatch.setattr(replay, "WB", stub)
    monkeypatch.setattr(replay, "GR", stub)
    monkey_path = server.REPLAY_DIR
    replay.record([WID], out_dir=monkey_path)
    fr = _frames(client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "decision", "replay": True}).text)
    assert fr[0][0] == "start" and fr[-1][0] == "result" and len(fr) > 3
    assert client.get("/api/config").json()["replay_available"] is True
    assert client.get("/api/waves").json()[0]["replay_available"] is True


def test_default_chain_uses_benchmark_winner(client):
    server.BENCHMARK.write_text(json.dumps({"selection": {"winner": "baseten/elephant-1"}, "arms": []}))
    chain = server._default_chain()
    assert chain[0] == "openrouter:baseten/elephant-1" and chain[-1] == "formula"
    assert len(chain) == len(set(chain))
    assert client.get("/api/config").json()["default_model"] == "baseten/elephant-1"
    j = client.post("/api/negotiate-wave", json={"wave_id": WID, "mode": "decision"})
    start = _frames(j.text)[0][1]
    assert start["model_chain"] == [server._model_id(e) for e in chain]


def test_v2_validation_error_shape(client):
    r = client.post("/api/negotiate-wave", content="[1,2]", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    r = client.post("/api/negotiate-wave", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "error" in r.json()
    assert client.get("/api/waves?limit=abc").json()["error"]["code"] == "invalid_request"
    # v1 route keeps FastAPI's default shape
    assert "detail" in client.get("/api/scenario/abc").json()


def test_note_non_string(client):
    r = client.post("/api/decision", json={"run_id": "x", "decision": "OVERRIDE", "note": 123})
    assert r.status_code == 200  # "123" is a non-empty note
    r = client.post("/api/decision", json={"run_id": "x", "decision": "OVERRIDE", "note": ["", None][1]})
    assert r.status_code == 422


def test_stub_only_when_module_missing(monkeypatch):
    import importlib
    from atm.wave import loader
    real = importlib.import_module

    def boom(name, *a, **k):
        if name == "atm.wave.graph":
            raise ModuleNotFoundError("No module named 'langgraph'", name="langgraph")
        return real(name, *a, **k)

    monkeypatch.setattr(loader.importlib, "import_module", boom)
    with pytest.raises(ModuleNotFoundError):
        loader._load("graph")

    def missing(name, *a, **k):
        if name == "atm.wave.graph":
            raise ModuleNotFoundError("No module named 'atm.wave.graph'", name="atm.wave.graph")
        return real(name, *a, **k)

    monkeypatch.setattr(loader.importlib, "import_module", missing)
    assert loader._load("graph").__name__ == "atm.wave.stub"
    monkeypatch.setenv("ATM_REQUIRE_REAL", "1")
    with pytest.raises(ModuleNotFoundError):
        loader._load("graph")


def test_xff_rightmost(client):
    body = {"wave_id": WID, "mode": "formulas", "stream": False}
    for i in range(10):  # spoofed leftmost entries must not dodge the limit
        h = {"x-forwarded-for": f"1.1.1.{i}, 9.9.9.9"}
        assert client.post("/api/negotiate-wave", json=body, headers=h).status_code == 200
    assert client.post("/api/negotiate-wave", json=body, headers={"x-forwarded-for": "2.2.2.2, 9.9.9.9"}).status_code == 429
    assert client.post("/api/negotiate-wave", json=body, headers={"x-forwarded-for": "9.9.9.8"}).status_code == 200
