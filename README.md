# Disruption Negotiator: Autonomous SG 2026, Airbus Challenge 2

**Jev decides · Claude explains · rules guarantee safety · the duty manager approves.**

When weather or a late inbound aircraft disrupts an arrival, the airline's hub duty manager
coordinates by phone between the tower, the crew and ground handling. This prototype replaces
those calls with three independent agents that negotiate a resolution in 3 rounds or fewer,
under a deterministic safety engine, and hands the result to the duty manager to approve.

## How it works

| Part | Who | What |
|---|---|---|
| Real disruption | US BTS on-time data 2023-2025, rows with `WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20` (11,914 flights) | The flight, its delays and the destination weather |
| Airport state | Simulated, seeded per flight (`atm/scenario.py`) | Runway queue and closures, taxiway closure, gates, fuel, crew shift, connections |
| ATC / PILOT / GROUND agents | Separate calls, each with a **private view** of its own domain only | Propose plans (runway, landing time, gate, or divert), vote on others' plans |
| Decision models | `atm/plans.py:utility` (formulas), or **Jev** (`atm/jev.py`): Choice to propose, Noul to accept with a probability, Score for severity triage | Each agent's own objectives |
| Safety engine | Deterministic code, 8 rules (`atm/plans.py:constraints`) | Wake separation, runway open, taxi route, earliest ETA, fuel reserve, gate size, gate free, crew duty. Vetoes any plan, whoever proposed it |
| Protocol | `atm/negotiation.py` | Propose -> engine check -> vote -> consensus (max-min utility). Agents concede each round; 3 rounds max, then a deterministic fallback solver |
| Narration | Claude (optional) | Turns the log into a brief for the duty manager. Decides nothing |
| Human | Dashboard | Approve or override, logged |

## Run

```bash
pip install fastapi uvicorn pandas pydantic anthropic typesafe-sdk
# optional keys in .env (gitignored): TYPESAFE_API_KEY=...  ANTHROPIC_API_KEY=...
uvicorn atm.server:app --port 8000      # dashboard at http://localhost:8000
python -m atm.evaluate --n 500 --compare jev --compare-n 50   # writes eval/REPORT.md
```

With no keys, everything runs on the decision-model formulas.

## Evaluation (500 real disrupted flights, formulas mode)

See `eval/REPORT.md` for the current numbers. Baseline = status quo: first free runway slot,
the scheduled gate (wait on the apron if it is busy), no cross-domain coordination.

| | Negotiated | Status quo |
|---|---|---|
| Mean delay at gate | 12.5 min | 41.6 min |
| Connecting pax protected | 90.7% | 64.0% |
| Holding fuel | 108 kg | 142 kg |
| Plans breaking a safety rule | 0 | 1 crew-duty breach |
| Agreed plans breaking a rule **with the engine switched off** | 97.6% | |

Consensus in 84.6% of flights (mean 1.9 rounds); 15.4% go to the deterministic fallback.

## Limits (read before trusting the numbers)

- The airport state around each real flight is simulated. Part of the delay gain comes from
  the simulated late outbound holding the scheduled gate, which the baseline waits for.
- The utility weights are design judgements, not calibrated to airline cost data.
- Wake separation is a time-based simplification of distance minima.
- Failure cases (fallbacks, plans burning more fuel than baseline) are listed in the report.

Data: `data/challenge2/disruptions.csv` is extracted by `scripts/extract_disruptions.py` from
the Challenge 5 BTS file; it also accepts the 2015 DOT `flights.csv` (Kaggle `usdot/flight-delays`).
