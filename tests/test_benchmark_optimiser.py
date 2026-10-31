import math

from atm.wave.benchmark import arm_vs_optimiser, fixture_waves, optimiser_summary, run_optimiser


def test_optimiser_summary_fixture_waves():
    waves = fixture_waves(3)
    opt = run_optimiser(waves, 2)
    common = {w.id for w in waves}
    assert set(opt) == common and all("error" not in r for r in opt.values())
    s = optimiser_summary(opt, common)
    for k in ("n", "delay_min", "delay_min_ci95", "fuel_kg", "pax_protected_pct", "n_feasible", "elapsed_ms_mean",
              "methods"):
        assert k in s
    assert s["n"] == 3 and 0 <= s["n_feasible"] <= 3
    assert all(math.isfinite(s[k]) for k in ("delay_min", "fuel_kg", "pax_protected_pct", "elapsed_ms_mean"))
    assert all(math.isfinite(x) for x in s["delay_min_ci95"])
    sq = {w: opt[w]["delay_min"] + 10 for w in common}
    arm = {w: opt[w]["delay_min"] + 2 for w in common}
    r = arm_vs_optimiser(arm, sq, common, opt, common)
    assert r["gap_to_optimiser"] == 2.0 and r["share_n"] == 3
    assert math.isclose(r["share_of_optimiser_gain_pct"], 80.0)
    assert arm_vs_optimiser(arm, sq, set(), opt, common)["share_of_optimiser_gain_pct"] is None
