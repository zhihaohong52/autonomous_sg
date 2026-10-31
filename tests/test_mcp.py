"""MCP tool tests: in-process, fixture-backed, no network."""
import asyncio

from atm import mcp_server
from atm.wave import build, fixtures, moves


def test_tools_registered():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"list_waves", "get_wave", "fcfs_plan", "legal_moves", "check_plan", "negotiate"}


def test_check_plan_tool_uses_engine(monkeypatch):
    w = fixtures.make_wave()
    monkeypatch.setattr(build, "build_wave", lambda wave_id: w)
    res = mcp_server.check_plan(w.id, moves.fcfs_plan(w).to_dict())
    assert "feasible" in res
