from atm.wave.benchmark import main

SUMMARY_KEYS = {"consensus_rate_pct", "mean_rounds", "fallback_rate_pct", "rule_violations_in_agreed_plans",
                "delay_min", "fuel_kg", "pax_protected_pct", "overtime_min", "agreement_with_formula_pct", "brier",
                "latency_p50_ms", "latency_p95_ms", "error_rate_pct", "cost_usd_per_wave"}


def test_small_benchmark(tmp_path):
    import json
    main(["--fixtures", "--n", "3", "--arms", "formula", "--concurrency", "2", "--ablation",
          "--outage-n", "0", "--out", str(tmp_path)])
    rep = json.loads((tmp_path / "benchmark.json").read_text())
    for k in ("generated_at", "n_waves", "seed", "arms", "baseline", "ablation_no_engine_unsafe_pct",
              "outage_test", "selection", "failures", "wave_ids"):
        assert k in rep
    assert rep["n_waves"] == 3 and len(rep["wave_ids"]) == 3
    for arm in rep["arms"]:
        assert set(arm["summary"]) == SUMMARY_KEYS
        assert arm["summary"]["rule_violations_in_agreed_plans"] == 0
    assert 0 <= rep["ablation_no_engine_unsafe_pct"] <= 100
    assert {"delay_min", "fuel_kg", "pax_protected_pct", "overtime_min", "diversions"} <= set(rep["baseline"])
    assert "n_feasible" in rep["baseline"] and 0 <= rep["baseline"]["n_feasible"] <= rep["n_common"]
    assert len(rep["baseline"]["infeasible_ids"]) == rep["n_common"] - rep["baseline"]["n_feasible"]
    assert (tmp_path / "BENCHMARK.md").read_text().startswith("# Benchmark")


def _fake_negotiate(real, dead_arm, fail_ids=()):
    def fake(wave, mode, chain=None, **kw):
        if (chain and chain[0] == dead_arm) or wave.id in fail_ids:
            raise RuntimeError("boom")
        return real(wave, mode, chain=chain, **kw)
    return fake


def test_dead_arm_cannot_win(tmp_path, monkeypatch):
    import json
    from atm.wave import benchmark
    monkeypatch.setattr(benchmark, "negotiate_wave", _fake_negotiate(benchmark.negotiate_wave, "openrouter:dead/m"))
    rep = benchmark.main(["--fixtures", "--n", "3", "--arms", "openrouter:dead/m,formula", "--concurrency", "2",
                          "--no-ablation", "--outage-n", "1", "--out", str(tmp_path)])
    assert rep["selection"]["winner"] != "dead/m"
    dead = next(a for a in rep["arms"] if a["model"] == "dead/m")
    assert dead["summary"]["brier"] is None and dead["summary"]["latency_p95_ms"] is None
    assert dead["n_ok"] == 0 and rep["ablation_no_engine_unsafe_pct"] is None
    json.loads((tmp_path / "benchmark.json").read_text())


def test_failed_waves_excluded_from_means(tmp_path, monkeypatch):
    from atm.wave import benchmark
    ids = [w.id for w in benchmark.fixture_waves(3)]
    monkeypatch.setattr(benchmark, "negotiate_wave",
                        _fake_negotiate(benchmark.negotiate_wave, None, fail_ids={ids[0]}))
    rep = benchmark.main(["--fixtures", "--n", "3", "--arms", "formula", "--concurrency", "1",
                          "--no-ablation", "--outage-n", "0", "--out", str(tmp_path)])
    assert rep["n_common"] == 2
    f = rep["arms"][0]
    assert f["n_ok"] == 2 and f["failed_ids"] == [ids[0]]
    assert f["summary"]["error_rate_pct"] > 0 and f["summary"]["consensus_rate_pct"] < 100
    assert any("1/3 waves failed" in r for r in rep["selection"]["reasons"])


def test_decisions_and_brier_read_agent_turns():
    from atm.wave.benchmark import brier, decisions
    mdl = {"kind": "noul", "model": "m", "provider": "openrouter", "latency_ms": 300}
    mv = {"kind": "choice", "model": "m", "provider": "openrouter", "latency_ms": 200, "fallback_from": ["x"]}
    fm = {"kind": "formula", "model": "formula", "provider": "formula", "latency_ms": 0}
    votes = [{"plan_id": "a", "vote": "ACCEPT", "p_accept": 0.8, "utility": 0.7, "reservation": 0.6},
             {"plan_id": "b", "vote": "REJECT", "p_accept": 0.5, "utility": 0.5, "reservation": 0.6}]
    log = [{"type": "TRIAGE", "round": 0, "body": {"type": "TRIAGE"}, "decision": fm},
           {"type": "AGENT_TURN", "round": 1, "decision": mdl,
            "body": {"type": "AGENT_TURN", "decision": mdl, "votes": votes,
                     "moves": [{"decision": mv}], "rejected": [{"decision": mv}]}},
           {"type": "AGENT_TURN", "round": 1, "body": {"type": "AGENT_TURN", "votes": [], "moves": [], "rejected": []}}]
    ds = decisions({"log": log})
    assert len(ds) == 4 and sum(1 for d in ds if d.get("fallback_from")) == 2
    assert brier([{"log": log}]) == round(((0.8 - 1) ** 2 + (0.5 - 0) ** 2) / 2, 4)


def test_outage_uses_best_model_arm_even_if_formula_wins():
    from atm.wave.benchmark import select
    summ = lambda d: {"delay_min": d, "pax_protected_pct": 90, "brier": 0.1, "latency_p95_ms": 100.0,
                      "error_rate_pct": 0.0, "consensus_rate_pct": 100.0, "fallback_rate_pct": 0.0}
    arm = lambda m, d, p: {"model": m, "provider": p, "label": m, "n_ok": 3, "failed_ids": [], "p95_all_ms": 100.0,
                           "summary": summ(d)}
    base = {"delay_min": 50}
    sel, best = select([arm("m1", 40, "openrouter"), arm("formula", 30, "formula")], 3, base)
    assert sel["winner"] == "formula" and best["model"] == "m1"
    sel, best = select([arm("formula", 30, "formula")], 3, base)
    assert best is None


def test_high_fallback_arm_with_better_delay_loses():
    from atm.wave.benchmark import select
    def arm(m, d, cons, fb, p="openrouter"):
        return {"model": m, "provider": p, "label": m, "n_ok": 50, "failed_ids": [], "p95_all_ms": 100.0,
                "summary": {"delay_min": d, "pax_protected_pct": 90, "brier": 0.1, "latency_p95_ms": 100.0,
                            "error_rate_pct": 0.0, "consensus_rate_pct": cons, "fallback_rate_pct": fb}}
    sel, best = select([arm("fast", 200, 68.0, 32.0), arm("steady", 250, 95.0, 5.0),
                        arm("formula", 300, 98.0, 2.0, "formula")], 50, {"delay_min": 400})
    assert sel["winner"] == "steady"
    assert any("fast missed consensus >= 90%: 68.0%" in r for r in sel["reasons"])
    assert any("fast outcomes partly from solver fallback on 16/50 waves" in r for r in sel["reasons"])
