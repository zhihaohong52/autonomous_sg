import os
from types import SimpleNamespace as NS

import pytest

from atm import decide
from atm.decide import Decider


@pytest.fixture(autouse=True)
def _clean():
    decide._reset_breakers()
    decide._reset_clients()
    yield
    decide._reset_breakers()
    decide._reset_clients()


def resp(**answers):
    return NS(answers=answers)


def test_falls_through_to_formula_when_all_fail(monkeypatch):
    chain = ["openrouter:a", "openrouter:b", "typesafe:jev"]
    def boom(self, entry, state, questions):
        raise RuntimeError("down")
    monkeypatch.setattr(Decider, "_call", boom)
    calls = []
    d = Decider(chain, on_fallback=lambda f, n, e: calls.append((f, n, e)))
    r = d.choice({"x": 1}, "pick", {"a": "A", "b": "B"}, lambda: "a")
    assert r.value == "a" and r.provider == "formula"
    assert r.fallback_from == chain
    assert len(calls) == len(chain)
    assert calls[-1][1] == "formula"


def test_invalid_choice_is_failure(monkeypatch):
    def fake(self, entry, state, questions):
        c = "zzz" if entry == "openrouter:a" else "b"
        return resp(q=NS(choice=c, probabilities={"b": 0.9}, confidence=0.8))
    monkeypatch.setattr(Decider, "_call", fake)
    r = Decider(["openrouter:a", "openrouter:b"]).choice({}, "pick", {"a": "A", "b": "B"}, lambda: "a")
    assert r.value == "b" and r.fallback_from == ["openrouter:a"]
    assert r.provider == "openrouter" and r.model == "b"


def test_breaker_opens_after_3(monkeypatch):
    seen = []
    def fake(self, entry, state, questions):
        seen.append(entry)
        if entry == "openrouter:bad":
            raise RuntimeError("x")
        return resp(q=NS(noul=0.7))
    monkeypatch.setattr(Decider, "_call", fake)
    d = Decider(["openrouter:bad", "openrouter:ok"])
    for _ in range(3):
        d.noul({}, "q", lambda: 0.5)
    assert seen.count("openrouter:bad") == 3
    r = d.noul({}, "q", lambda: 0.5)
    assert seen.count("openrouter:bad") == 3
    assert r.value == 0.7 and "circuit open" in r.errors[0]


def test_noul_batch_one_request(monkeypatch):
    n = []
    def fake(self, entry, state, questions):
        n.append(questions)
        assert set(questions) == {"q0", "q1", "q2"}
        return resp(q0=NS(noul=0.1), q1=NS(noul=0.2), q2=NS(noul=0.3))
    monkeypatch.setattr(Decider, "_call", fake)
    out = Decider(["openrouter:a"]).noul_batch({}, {"p-1": "i1", "p-2": "i2", "p-3": "i3"}, lambda i: 0.0)
    assert len(n) == 1
    assert {k: v.value for k, v in out.items()} == {"p-1": 0.1, "p-2": 0.2, "p-3": 0.3}


def test_score_out_of_range_is_failure(monkeypatch):
    monkeypatch.setattr(Decider, "_call", lambda self, e, s, q: resp(q=NS(score=7.2, probabilities=None, confidence=None)))
    r = Decider(["openrouter:a"]).score({}, "sev", ["l0", "l1", "l2"], lambda: 1)
    assert r.provider == "formula" and r.value == 1 and r.fallback_from == ["openrouter:a"]


def test_one_retry_on_retryable(monkeypatch):
    n = []
    def fake(self, entry, state, questions):
        n.append(entry)
        raise TimeoutError("slow")
    monkeypatch.setattr(Decider, "_call", fake)
    r = Decider(["openrouter:a"]).noul({}, "q", lambda: 0.5)
    assert n == ["openrouter:a"] * 2 and r.provider == "formula"


def test_no_retry_on_non_retryable(monkeypatch):
    n = []
    def fake(self, entry, state, questions):
        n.append(entry)
        raise RuntimeError("nope")
    monkeypatch.setattr(Decider, "_call", fake)
    Decider(["openrouter:a"]).noul({}, "q", lambda: 0.5)
    assert len(n) == 1


def test_fallback_hook_called(monkeypatch):
    def fake(self, entry, state, questions):
        raise RuntimeError("down")
    monkeypatch.setattr(Decider, "_call", fake)
    calls = []
    Decider(["openrouter:a", "openrouter:b"], on_fallback=lambda f, n, e: calls.append((f, n, e))).noul(
        {}, "q", lambda: 0.5)
    assert [(f, n) for f, n, _ in calls] == [("openrouter:a", "openrouter:b"), ("openrouter:b", "formula")]
    assert all(isinstance(e, str) and e for _, _, e in calls)


def test_missing_openrouter_key_never_builds_client(monkeypatch):
    import typesafe_sdk
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy")
    def forbidden(*a, **kw):
        pytest.fail(f"client constructed: {kw.get('base_url')}")
    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", forbidden)
    r = Decider(["openrouter:x/y"]).noul({}, "q", lambda: 0.5)
    assert r.provider == "formula"
    assert any("OPENROUTER_API_KEY not set" in e for e in r.errors)


def test_latency_includes_failures(monkeypatch):
    import time
    def fake(self, entry, state, questions):
        time.sleep(0.05)
        raise RuntimeError("down")
    monkeypatch.setattr(Decider, "_call", fake)
    r = Decider(["openrouter:a", "openrouter:b"]).noul({}, "q", lambda: 0.5)
    assert r.latency_ms >= 100


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 for network smoke test")
def test_live_smoke():
    r = Decider(["openrouter:liquid/d1-ultra"], timeout_s=10.0).noul(
        {"flight": "AB123 delayed 20 min by weather"}, "Is the flight delayed?", lambda: 0.5)
    print("live latency_ms", r.latency_ms, "attempt_ms", r.attempt_ms, r.errors)
    assert r.provider == "openrouter" and 0 <= r.value <= 1
