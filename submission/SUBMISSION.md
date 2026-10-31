# Autonomous SG 2026 submission: Final Approach

Track: Airbus · Problem: Air traffic decisions · Challenge 2

All numbers below come from `README.md` (BENCHMARK block, sections 1-5) and `eval/BENCHMARK.md` (run of 31 Oct 2026, 50 seeded `flights.csv` rows, seed 7).

---

## 1. Submission title

Final Approach: ATC, Pilot and Ground agents agree a rule-checked plan

---

## 2. Project materials link

https://github.com/zhihaohong52/final_approach

Deck video: https://youtu.be/JpgnufOeDQQ

Cover image: `docs/architecture.png` (1920×1080 PNG, under 2 MB). Alternative: `deck/poster.png` (title slide).

Live demo: https://disruption-negotiator.onrender.com (Render free tier, allow about 1 minute for a cold start)

---

## 3. Summary

When weather or a late inbound aircraft disrupts an arrival, an airline duty manager today coordinates the fix by phone with the tower, the crew and ground handling. Final Approach replaces those calls with three AI agents.

Pick any disrupted flight from the challenge's flights.csv. The app rebuilds its arrival wave with the airport's real runways, gates and weather. ATC, the Pilot and Ground then negotiate a revised arrival time, runway and gate, each acting only within its own authority, for at most 3 turns. A deterministic engine checks every move against 12 safety rules, so no model can approve an unsafe plan. If the agents cannot agree, a deterministic solver decides and the log says why. Claude writes a short brief, and the duty manager approves or overrides.

Across 1,000 real disruptions the agents agreed on 99.5% of waves, mostly in the first turn, with zero rule violations.

---

## 4. Q4 answer (<= 500 characters)

Working prototype. Live at disruption-negotiator.onrender.com. Pick any disrupted flights.csv row: ATC, Pilot and Ground agents negotiate in at most 3 turns, a 12-rule deterministic engine checks every move, a solver takes over if they do not agree, and the duty manager approves or overrides. Benchmarked on 50 rows: 0 rule violations in every arm, 98% consensus with jev-1.14 within 1.4 min of an optimiser, status quo breaks a rule on 16 of 50 waves, 20 of 20 waves resolved in the outage test.

Character count: 497 (checked with Python `len()`, limit 500).

---

## 5. Three-minute pitch script

Before going on stage: open https://disruption-negotiator.onrender.com 2 minutes early so the free tier wakes up. Have the Benchmark page open in a second tab.

### Problem (0:00 to 0:30)

"Strong crosswind hits Dallas and closes runways while a delayed arrival is inbound. Its runway, slot and gate all have to change. Today the airline duty manager fixes this by phone: the tower, the crew, ground handling, one call at a time. Nobody checks the combined answer against wake separation, fuel, crew duty and gate rules together. We built three agents that make those calls for them, with a rule engine that no model can overrule."

### Live demo (0:30 to 2:00)

Click path:

1. Wave dropdown in the toolbar: choose `F2015-75` (DFW, crosswind runway closures). The recorded runs are listed first; if it is not there, open "Flight from flights.csv" and search AA1344.
2. Open the settings menu next to "Random wave": Decision mode "Decision models" (the default), model jev-1.14, tick "Replay the recorded run" (no network risk on stage). Click **Start negotiation**.
3. Watch the "Runways and gates" timeline: flights move as agents make moves; a vetoed move shows "Vetoed:" with the rule. When it finishes, flip the **Before | After** toggle to compare the status quo with the agreed plan (the Safety rules tab has the same toggle).
4. Open the **Negotiation log** tab: one AGENT_TURN per agent, ATC then PILOT then GROUND, each with a "what changed" diff; tick "Show model, probability and latency" to see jev-1.14's P(accept) on every vote.
5. Open the **Safety rules** tab: every rule for every flight, with source and values.
6. Read the result: AA1344 on 13L, gate A38, +119 min, agreed in turn 1. Point at the brief and its line "it decides nothing".
7. Failure case: search flights.csv for `F2015-102056` (DL2019; runs live, about 10 s). In all 3 turns no plan passes the engine CHECK, so the log shows three NO_CONSENSUS messages and a FALLBACK with its reason; the safety net diverts DL2019 to CSG. Backup if time is short: `F2015-69315` (ATL fog), where the agents only agree in turn 2 after conceding.
8. Click **Override**, show that a note is required, then **Cancel** and **Approve plan**.

Voice-over:

"This is a real row from the challenge file, AA1344 into Dallas, with the real runways and the real METAR at that hour. ATC goes first and resequences the runway. The pilot answers for its own flight. Ground fixes the gate. Every move goes through the engine; when one breaks a rule it is vetoed on screen, with the rule and the numbers. Here is the log: one structured message per agent per turn, and the model's probability for every pick. The safety tab shows each of the 12 rules, where the limit comes from and what values it used. Result: runway 13L, gate A38, agreed in the first turn. Claude writes the brief, but it only explains; it changes nothing. Now a case where it fails. Here no plan the agents can reach passes the safety check in 3 turns, so the system refuses to invent one: a deterministic solver takes over, and the log says exactly why. Finally, the duty manager decides. Override needs a written reason, which is logged. Approve."

### Results (2:00 to 2:45)

Switch to the **Benchmark** tab.

"We tested on 50 seeded rows across 30 hubs. Zero rule violations in every final plan, in every arm. The status quo, with no coordination, breaks a rule on 16 of 50 waves. Switch our engine off and 32% of the agents' plans are illegal, so the engine is doing real work. Our default model, jev-1.14, reaches 98% consensus in 1.32 turns and lands within 1.4 minutes of a central optimiser, exactly matching our deterministic formulas. That only happened once code did the maths and the model just judged. Three self-interested agents lose almost nothing against central control. Kill the main model, and 20 of 20 waves still resolve. Honestly: delay barely moves, about 2 minutes better where the status quo is legal. What negotiation buys is legality, in a few seconds per wave."

### Close (2:45 to 3:00)

"Decision models judge, code does the maths, rules guarantee safety, Claude explains, and the duty manager decides. It runs live on any disrupted row in the file. Thank you."

---

## 6. Likely judge questions

**How did you pick the model, and why not just use the formulas?**
The benchmark chooses with a fixed rule: best outcome among arms with error < 2%, p95 < 1 s and consensus >= 90%. jev-1.14 won: it matches the formulas (98% consensus, 351.7 min, 1.4 min from the optimiser). The formulas stay as the always-available fallback and as the reference the models are measured against. Early on the models lagged (68-94% consensus); giving them pre-computed verdicts instead of raw numbers, per TypeSafe's System One guidance, closed the gap.

**Why do the agents speak in turn instead of all at once?**
Each turn is chained: ATC starts from the plan on the table, PILOT builds on ATC's proposal and GROUND on PILOT's, so each agent reacts to the others with exactly one structured message per turn. That keeps the roles separate (never one prompt playing all three), bounds the run to at most 3 turns, and gives a log that reads like the phone calls it replaces.

**What is simulated?**
Gate occupancy, fuel on board, crew shifts, passenger connections, and two wide gates at hubs where OpenStreetMap has none are seeded simulations, badged "simulated" in the UI. Flights, delays, runways, weather and gates are real; aircraft types are real where the FAA registry has the tail and generic, badged, otherwise. The workbook limits are the challenge's synthetic planning numbers.

**What do you assume for taxi time?**
6 min for a near gate and 10 min for a far one (runway and pier paired by halves), and a remote stand counts as far plus 10 min by bus, with no taxiway closures. The data has gate names but no gate positions or taxiway network, so this is a stated assumption, listed in the limits the app returns.

**How is safety guaranteed?**
Only the deterministic rule engine decides legality: every move and every proposal is checked, agents vote only on plans that passed the check, the final plan is checked again, and models can only pick among options the engine offered. If nothing passes, the system does not invent a plan: the solver's safety net (remote stand, ground delay or divert) takes over, and the benchmark shows 0 violations in every final plan.

**How many rounds does it take to agree?**
On 999 waves (1,000-row run), 80.7% agree in turn 1, 18.6% in turn 2 and 0.2% in turn 3; 0.5% never agree because no reachable plan passes the rules, and the solver decides (mean 1.20 turns). Turn 2 exists mostly because Ground speaks last, so ATC and the Pilot can only vote on Ground's plan in the next turn.

**Isn't a 0.5 acceptance threshold arbitrary?**
We follow TypeSafe's confidence routing: a model vote counts only when confident, P(accept) >= 0.7 accepts and <= 0.3 rejects; anything in between falls back to the formula verdict and the log marks it `decided_by: formula`. Because code hands the model pre-computed verdicts, its votes are near 0 or 1: in the recorded runs none landed in the band.

**Is the delay result significant?**
At n = 50 the 95% intervals are about ±47 min, so the delay differences between arms are not significant; at 1,000 rows jev-1.14 and the formulas are 329.8 vs 329.3 ± 10.6 min with the same outcome on every wave. The claims we make are about legality (0 violations vs 16/50 for the status quo), consensus and robustness, which hold at both sizes.

