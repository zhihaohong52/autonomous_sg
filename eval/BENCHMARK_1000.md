# Scale run: 1,000 flights.csv rows (31 Oct 2026)

Four parallel shards of `python3 -m atm.wave.benchmark --source flights2015 --n 250 --arms openrouter:typesafe/jev-1.14 --concurrency 12 --seed {101,102,103,104} --outage-n 0 --no-ablation` (999 unique waves; one row drawn by two seeds). Live TypeSafe jev-1.14 through OpenRouter; the formula arm runs on the same waves. Aggregated from the per-wave log lines.

| Arm | Waves | Consensus | Mean turns | Mean total delay (min) ± 95% CI |
|---|---|---|---|---|
| typesafe/jev-1.14 | 999 | 99.5% | 1.20 | 329.8 ± 10.6 |
| formula | 999 | 99.5% | 1.20 | 329.3 ± 10.6 |

Same outcome (consensus or fallback) as the formulas on 100% of waves; paired mean delay difference 0.5 min. The optimiser and status-quo references for this run were still computing at submission time; see `eval/BENCHMARK.md` (n = 50) for those.
