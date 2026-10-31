"""Model votes act only when confident: P >= 0.7 accepts, P <= 0.3 rejects, the band in between defers to the formula."""
from types import SimpleNamespace as NS

from atm.decide import Decider
from atm.wave.graph import negotiate_wave
from tests.fixtures import make_wave


def _run(monkeypatch, p):
    def fake(self, entry, state, questions):
        q = next(iter(questions.values()))
        if q.type == "noul":
            return NS(answers={k: NS(noul=p) for k in questions})
        if q.type == "choice":  # make no further move when allowed, else the first option
            key = next((k for k in q.criteria if "no further move" in str(q.criteria[k]).lower()), next(iter(q.criteria)))
            return NS(answers={"q": NS(choice=key, probabilities={key: 0.9}, confidence=0.9)})
        return NS(answers={"q": NS(score=0, probabilities=None, confidence=0.9)})
    monkeypatch.setattr(Decider, "_call", fake)
    return negotiate_wave(make_wave(), "decision", chain=["openrouter:x/y"])


def _votes(r):
    return [v for m in r["log"] if m["type"] == "AGENT_TURN" for v in m["body"]["votes"]]


def test_uncertain_band_defers_to_formula(monkeypatch):
    vs = _votes(_run(monkeypatch, 0.55))
    model_votes = [v for v in vs if "p_accept" in v]
    assert model_votes and all(v.get("decided_by") == "formula" for v in model_votes)
    assert all((v["vote"] == "ACCEPT") == (v["utility"] >= v["reservation"]) for v in model_votes)


def test_confident_model_vote_stands(monkeypatch):
    vs = [v for v in _votes(_run(monkeypatch, 0.9)) if "p_accept" in v]
    assert vs and all(v["vote"] == "ACCEPT" and "decided_by" not in v for v in vs)
