# Disruption Negotiator (Autonomous SG 2026, Airbus Challenge 2)

Design: `docs/superpowers/specs/2026-10-31-disruption-negotiator-design.md` · API contract: `docs/api-contract.md`

## Delegating to subagents (`.claude/agents/`)
Pick the cheapest agent that can do the job:
- `explorer` (Haiku): read-only lookups, file/data/docs reading.
- `coder-simple` (Haiku): one- or two-file changes fully specified up front.
- `coder` (Sonnet): normal features where the what is clear (endpoints, loaders, fetch scripts, React components).
- `architect` (Opus): design decisions, negotiation/safety/decision-layer core, unclear bugs, correctness review.
If a cheaper agent reports the task needs a decision or fails twice, move it up one tier.

## Rules
- Secrets live in `.env` (gitignored). Never print or commit them.
- Frontend (`frontend/`) is owned by the teammate, except `frontend/src/api/types.ts` and `frontend/src/api/samples/`, which the backend writes.
- Contract changes go in commits prefixed `contract:`.
