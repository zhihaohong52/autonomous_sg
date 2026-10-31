"""FastAPI app: run `uvicorn atm.server:app --reload` and open http://localhost:8000."""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import baseline, jev, negotiation, scenario

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
EVAL = ROOT / "eval" / "results.json"
DECISIONS = ROOT / "eval" / "decisions.jsonl"

app = FastAPI(title="ATM disruption negotiation")


def _summary(fid: int) -> dict:
    f = scenario.disruptions().set_index("id").loc[fid]
    return {"id": int(fid), "callsign": f"{f.carrier}{int(f.flight_no)}", "route": f"{f.origin}->{f.dest}",
            "date": f"{int(f.year)}-{int(f.month):02d}-{int(f.day):02d}", "weather_delay": f.weather_delay,
            "late_aircraft_delay": f.late_aircraft_delay}


@app.get("/api/config")
def config():
    return {"llm_available": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            "jev_available": jev.available(), "model": negotiation.MODEL,
            "policies": ["rule", "jev", "jev+claude", "llm"], "n_disruptions": len(scenario.disruptions())}


@app.get("/api/disruptions")
def disruptions(q: str = "", limit: int = 50):
    df = scenario.disruptions()
    if q:
        q = q.upper()
        df = df[(df.dest == q) | (df.origin == q) | (df.carrier + df.flight_no.astype(str) == q)]
    return [_summary(i) for i in df.id.head(limit)]


@app.get("/api/random")
def random_disruption():
    return _summary(random.choice(scenario.disruptions().id.tolist()))


@app.get("/api/scenario/{fid}")
def get_scenario(fid: int):
    try:
        return scenario.build(fid).to_dict()
    except KeyError:
        raise HTTPException(404, "unknown flight id")


class NegotiateReq(BaseModel):
    flight_id: int
    policy: str = "rule"


@app.post("/api/negotiate")
def negotiate(req: NegotiateReq):
    sc = scenario.build(req.flight_id)
    res = negotiation.negotiate(sc, req.policy)
    return {"scenario": sc.to_dict(), "result": res, "baseline": baseline.run(sc)}


class Decision(BaseModel):
    flight_id: int
    plan_id: str
    decision: str  # APPROVE | OVERRIDE
    note: str = ""


@app.post("/api/decision")
def decision(d: Decision):
    DECISIONS.parent.mkdir(exist_ok=True)
    with DECISIONS.open("a") as fh:
        fh.write(json.dumps({**d.model_dump(), "ts": time.time()}) + "\n")
    return {"ok": True}


@app.get("/api/eval")
def eval_results():
    if not EVAL.exists():
        raise HTTPException(404, "run `python -m atm.evaluate` first")
    return json.loads(EVAL.read_text())


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
