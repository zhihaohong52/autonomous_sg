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
