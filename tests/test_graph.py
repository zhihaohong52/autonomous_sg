import os
from dataclasses import replace

import pytest

from atm import decide
from atm.decide import Decider
from atm.wave import graph, rules
from atm.wave.graph import negotiate_wave
from atm.wave.model import JointPlan
from tests.fixtures import make_wave

CONTRACT_TYPES = {"TRIAGE", "OPEN", "MOVE", "MOVE_REJECTED", "COUNTER", "CHECK", "VOTE", "CONSENSUS",
                  "NO_CONSENSUS", "FALLBACK", "MODEL_FALLBACK"}
ROUND_ORDER = {"MOVE": 0, "MOVE_REJECTED": 0, "COUNTER": 1, "CHECK": 2, "VOTE": 3, "CONSENSUS": 4,
               "NO_CONSENSUS": 4}
RESULT_KEYS = {"run_id", "wave_id", "mode", "model", "outcome", "rounds", "final_plan", "evaluation", "baseline",
               "narration", "model_errors", "stats", "log"}


@pytest.fixture(autouse=True)
def _clean():
    decide._reset_breakers()
    decide._reset_clients()
    yield
    decide._reset_breakers()
    decide._reset_clients()


def _hard_wave():
    w = make_wave()
    m = replace(w.metar, raw="KATL 151551Z 36040G55KT 2SM +TSRA OVC008 22/21 A2970", wind_dir_deg=360,
                wind_kt=40, gust_kt=55, visibility_sm=2, wx_codes=["+TSRA"], surface="wet")
    return replace(w, metar=m)


def _plan(wave, d: dict) -> JointPlan:
    from atm.wave.model import Assignment
    return JointPlan([Assignment(**{k: v for k, v in a.items() if k != "metrics"}) for a in d["assignments"]])


def log_of(r):
    return r["log"]


def _assert_valid(wave, r):
    assert RESULT_KEYS <= set(r)
    assert r["outcome"] in {"consensus", "fallback"}
    assert 1 <= r["rounds"] <= 3
    assert r["evaluation"]["feasible"]
    assert rules.feasible(wave, _plan(wave, r["final_plan"]))
    assert r["final_plan"]["id"] == r["evaluation"]["plan_id"]
    assert r["final_plan"]["label"] == _plan(wave, r["final_plan"]).describe()
    r3_votes = {m["sender"]: m["body"]["votes"][0]["reservation"] for m in log_of(r)
                if m["type"] == "VOTE" and m["round"] == 3 and m["body"]["votes"]}
    for m in log_of(r):
        if m["type"] == "NO_CONSENSUS" and m["round"] == 3:  # no round 4: current reservations
            for ag, res in r3_votes.items():
                assert m["body"]["next_reservation"][ag] == res
    log = r["log"]
    assert [m["seq"] for m in log] == list(range(len(log)))
    for m in log:
        assert m["type"] in CONTRACT_TYPES
        assert m["body"]["type"] == m["type"]
        assert isinstance(m["rationale"], str) and "\n" not in m["rationale"]
        assert {"seq", "round", "ts", "sender", "type", "body", "rationale"} <= set(m)
        if "decision" in m:
            assert m["decision"]["kind"] in {"choice", "noul", "score", "formula"}
            assert m["decision"]["provider"] in {"openrouter", "typesafe", "formula"}


def test_formulas_run_terminates_valid():
    w = make_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert r["model"] == "formula" and r["narration"] is None
    assert r["stats"]["model_calls"] == 0 and r["stats"]["fallbacks"] == 0
    assert all(m["decision"]["kind"] == "formula" for m in r["log"] if "decision" in m)
    for m in r["log"]:
        if m["type"] == "VOTE":
            assert all("p_accept" not in v for v in m["body"]["votes"])
    assert r["baseline"]["plan"]["id"] == r["baseline"]["evaluation"]["plan_id"]


def _check_order(log):
    r0 = [m["type"] for m in log if m["round"] == 0 and m["type"] != "MODEL_FALLBACK"]
    assert r0 == ["TRIAGE", "OPEN"]
    last_round = max(m["round"] for m in log)
    for rnd in range(1, last_round + 1):
        types = [m["type"] for m in log if m["round"] == rnd and m["type"] not in ("MODEL_FALLBACK", "FALLBACK")]
        ranks = [ROUND_ORDER[t] for t in types]
        assert ranks == sorted(ranks), types
        assert types[-1] in ("CONSENSUS", "NO_CONSENSUS")
        assert types.count("CONSENSUS") + types.count("NO_CONSENSUS") == 1
    rounds_seq = [m["round"] for m in log]
    assert rounds_seq == sorted(rounds_seq)
    fb = [i for i, m in enumerate(log) if m["type"] == "FALLBACK"]
    if fb:
        assert len(fb) == 1 and fb[0] == len(log) - 1
        assert last_round == 3
        assert log[fb[0] - 1]["type"] == "NO_CONSENSUS" and log[fb[0] - 1]["round"] == 3


def test_message_order():
    for w in (make_wave(), _hard_wave()):
        _check_order(negotiate_wave(w, "formulas")["log"])


def test_providers_down_still_resolves(monkeypatch):
    def boom(self, entry, state, questions):
        raise RuntimeError("provider down")
    monkeypatch.setattr(Decider, "_call", boom)
    w = make_wave()
    r = negotiate_wave(w, "decision", chain=["openrouter:x/y"])
    _assert_valid(w, r)
    _check_order(r["log"])
    mf = [m for m in r["log"] if m["type"] == "MODEL_FALLBACK"]
    assert mf
    assert all(m["body"]["from_model"] == "openrouter:x/y" and m["body"]["to_model"] == "formula" for m in mf)
    assert all(m["body"]["agent"] == m["sender"] for m in mf)
    assert r["stats"]["fallbacks"] >= len(mf) and r["stats"]["model_calls"] == 0
    assert r["model_errors"]
    decided = [m for m in r["log"] if "decision" in m]
    assert decided and all(m["decision"]["kind"] == "formula" for m in decided)
    assert any(m["decision"].get("fallback_from") == ["openrouter:x/y"] for m in decided)


def test_on_message_streams():
    got = []
    r = negotiate_wave(make_wave(), "formulas", on_message=got.append, run_id="abc123")
    assert r["run_id"] == "abc123"
    assert got == r["log"]


def test_hard_wave_falls_back_safely():
    w = _hard_wave()
    r = negotiate_wave(w, "formulas")
    _assert_valid(w, r)
    assert any(m["type"] == "TRIAGE" and m["body"]["score"] >= 3 for m in r["log"])


def test_graph_mermaid_written(tmp_path):
    text = graph.mermaid()
    for node in ("triage", "open", "moves", "counter", "check", "vote", "decide_round", "concede", "fallback",
                 "finalize"):
        assert node in text
    path = tmp_path / "graph.mmd"   # docs/graph.mmd: python3 -m atm.wave.graph --mermaid docs/graph.mmd
    path.write_text(text)
    assert path.stat().st_size > 0
    assert "decide_round -.-> fallback" in text and "concede --> moves" in text


def test_narration_template_without_key(monkeypatch):
    from atm.wave import narrate
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = negotiate_wave(make_wave(), "formulas")
    n = narrate.brief(r)
    assert n["source"] == "template" and n["brief"] and n["lines"]
    assert n["error"] == "ANTHROPIC_API_KEY not set"
    r2 = negotiate_wave(make_wave(), "decision+claude", chain=[])
    assert r2["narration"]["source"] == "template" and "error" not in r2["narration"]
    assert "narration: ANTHROPIC_API_KEY not set" in r2["model_errors"]
    assert len(n["brief"].split()) <= narrate.MAX_WORDS + 1


def test_engine_rejects_unsafe_model_pick(monkeypatch):
    """A model that keeps picking moves which delay DL100 (only 1 min extra fuel) is vetoed by the engine
    with PLT-FUEL-RESERVE cited, at most 2 rejections per move slot, and the run still resolves safely."""
    from types import SimpleNamespace as NS

    bad = ("Shift DL100 slot +2", "Swap landing order DL100")

    def fake(self, entry, state, questions):
        q = questions.get("q")
        if getattr(q, "type", None) == "choice":
            pick = next((k for k, v in q.criteria.items() if v.startswith(bad)), None)
            if pick:
                return NS(answers={"q": NS(choice=pick, probabilities={pick: 0.9}, confidence=0.8)})
        raise RuntimeError("defer to formula")
    monkeypatch.setattr(Decider, "_call", fake)
    w = make_wave()
    w = replace(w, flights=[replace(f, fuel={**f.fuel, "extra_min": 1}) if f.callsign == "DL100" else f
                            for f in w.flights])
    r = negotiate_wave(w, "decision", chain=["openrouter:x/y"])
    _assert_valid(w, r)
    _check_order(r["log"])
    rej = [m for m in r["log"] if m["type"] == "MOVE_REJECTED"]
    assert rej
    for m in rej:
        assert m["sender"] == "ENGINE" and m["body"]["move"]["agent"] == "ATC"
        assert m["body"]["move"]["label"].startswith(bad)
        assert [v["rule"] for v in m["body"]["violations"]] == ["PLT-FUEL-RESERVE"]
        assert m["body"]["violations"][0]["evidence"]["extra_min"] == 1
        assert m["decision"]["kind"] == "choice" and m["decision"]["model"] == "x/y"
        assert list(m["decision"]["probabilities"]) == [m["body"]["move"]["id"]]
    for rnd in {m["round"] for m in rej}:
        assert sum(m["round"] == rnd for m in rej) <= 2  # both bad options exhausted within one slot
    assert not any(m["type"] == "MOVE" and m["body"]["move"]["label"].startswith(bad) for m in r["log"])
