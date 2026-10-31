"""Read-only MCP server exposing wave-mode tools. Mounted under /mcp by atm.server.

Legality is decided only by the deterministic rule engine (atm.wave.rules via utility.evaluate);
nothing here calls a paid model. Run standalone over stdio with `python -m atm.mcp_server`."""
from __future__ import annotations

import dataclasses

from mcp.server.fastmcp import FastMCP

from atm.wave import build, graph, moves, utility
from atm.wave.model import Assignment, JointPlan

mcp = FastMCP("disruption-negotiator", stateless_http=True)


def _plan_from_dict(d: dict) -> JointPlan:
    """Rebuild a JointPlan from its contract dict; metrics are recomputed by the engine, so they are dropped."""
    fields = {f.name for f in dataclasses.fields(Assignment)} - {"metrics"}
    assignments = [Assignment(**{k: v for k, v in a.items() if k in fields}) for a in d.get("assignments", [])]
    return JointPlan(assignments, d.get("label") or "")


@mcp.tool()
def list_waves(airport: str | None = None, q: str | None = None, limit: int = 20) -> list[dict]:
    """List disrupted arrival waves (summaries), optionally filtered by airport code or a flight/wave id
    substring. Read-only. Legality of any plan is decided only by the deterministic rule engine."""
    return build.list_waves(airport=airport, q=q, limit=limit)


@mcp.tool()
def get_wave(wave_id: str) -> dict:
    """Return the full wave scenario (flights, runways, weather, gates, queue) for one wave id.
    Read-only. Legality of any plan is decided only by the deterministic rule engine."""
    return build.build_wave(wave_id).to_dict()


@mcp.tool()
def fcfs_plan(wave_id: str) -> dict:
    """Return the first-come-first-served baseline plan for a wave. Read-only. Legality of the plan is
    decided only by the deterministic rule engine (see check_plan)."""
    return moves.fcfs_plan(build.build_wave(wave_id)).to_dict()


@mcp.tool()
def legal_moves(wave_id: str, agent_id: str, plan: dict | None = None) -> list[dict]:
    """List the moves an agent (e.g. ATC, GROUND, PILOT:<callsign>) may make from a plan, most useful first.
    The plan defaults to the FCFS plan. Read-only. Legality is decided only by the deterministic rule engine:
    infeasible moves are returned ranked lower so the engine's rejection is visible."""
    w = build.build_wave(wave_id)
    p = _plan_from_dict(plan) if plan is not None else moves.fcfs_plan(w)
    return [m.to_dict() for m in moves.legal_moves(w, p, agent_id)]


@mcp.tool()
def check_plan(wave_id: str, plan: dict) -> dict:
    """Evaluate a joint plan for a wave and return its PlanEvaluation (feasibility, violations, utilities).
    Read-only. Legality is decided only by the deterministic rule engine, never by the caller or a model."""
    return utility.evaluate(build.build_wave(wave_id), _plan_from_dict(plan))


@mcp.tool()
def negotiate(wave_id: str) -> dict:
    """Run the formulas-only negotiation for a wave (no paid model calls) and return the result without its
    message log. Read-only. Legality is decided only by the deterministic rule engine."""
    res = graph.negotiate_wave(build.build_wave(wave_id), "formulas")
    res.pop("log", None)
    return res


# Mounted at /mcp by atm.server. The SSE transport needs no lifespan hook (streamable HTTP would need
# mcp.session_manager.run() started in the parent app's lifespan).
mcp_asgi_app = mcp.sse_app()


if __name__ == "__main__":
    mcp.run()
