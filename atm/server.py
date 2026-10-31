"""FastAPI app: run `uvicorn atm.server:app --reload` and open http://localhost:8000."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import baseline, decide, jev, negotiation, scenario
from .mcp_server import mcp_asgi_app
from .wave import replay

from .wave.loader import _load

WB = _load("build")  # real module, or the fixture-backed stub only if the module file itself is missing
GR = _load("graph")

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
EVAL = ROOT / "eval" / "results.json"
DECISIONS = ROOT / "eval" / "decisions.jsonl"
BENCHMARK = ROOT / "eval" / "benchmark.json"
RUNS_DIR = ROOT / "eval" / "runs"
REPLAY_DIR = ROOT / "data" / "replay"

app = FastAPI(title="ATM disruption negotiation")


def _summary(fid: int) -> dict:
    f = scenario.disruptions().set_index("id").loc[fid]
    return {"id": int(fid), "callsign": f"{f.carrier}{int(f.flight_no)}", "route": f"{f.origin}->{f.dest}",
            "date": f"{int(f.year)}-{int(f.month):02d}-{int(f.day):02d}", "weather_delay": f.weather_delay,
            "late_aircraft_delay": f.late_aircraft_delay}


@app.on_event("startup")
def _warm() -> None:
    """Load the flights.csv index and the demo list in the background so the first page load is fast."""
    def run():
        try:
            from .wave import flights2015
            flights2015._index()
            _replay_summaries()
        except Exception:  # noqa: BLE001 - warming is best effort
            pass
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/config")
def config():
    # v1 keys (web/index.html) merged with the v2 Config contract.
    try:  # v1 disruptions.csv is excluded from the Docker image (.dockerignore)
        n_disruptions = len(scenario.disruptions())
    except OSError:
        n_disruptions = 0
    v1 = {"jev_available": jev.available(), "model": negotiation.MODEL,
          "policies": ["rule", "jev", "jev+claude", "llm"], "n_disruptions": n_disruptions}
    models, default_model = _models()
    return {**v1, "version": "2.0", "modes": MODES, "default_mode": _default_mode(), "models": models,
            "default_model": default_model,
            "llm_available": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            "tracing": bool(os.environ.get("LANGSMITH_API_KEY")),
            "replay_available": REPLAY_DIR.is_dir() and any(p.is_file() for p in REPLAY_DIR.iterdir())}


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
def decision(body: dict = Body(...)):
    if "run_id" in body:  # v2: {run_id, decision, note?}
        kind, note = body.get("decision"), str(body.get("note") or "").strip()
        if kind not in ("APPROVE", "OVERRIDE"):
            return _err(422, "invalid_decision", "decision must be APPROVE or OVERRIDE")
        if kind == "OVERRIDE" and not note:
            return _err(422, "note_required", "a note is required to OVERRIDE")
        now = datetime.now(timezone.utc).isoformat()
        DECISIONS.parent.mkdir(parents=True, exist_ok=True)
        with DECISIONS.open("a") as fh:
            fh.write(json.dumps({"run_id": str(body["run_id"]), "decision": kind, "note": note,
                                 "recorded_at": now, "ts": time.time()}) + "\n")
        return {"ok": True, "recorded_at": now}
    try:  # v1 behaviour, unchanged
        d = Decision(**body)
    except Exception as e:  # pydantic ValidationError -> same 422 shape FastAPI used
        raise HTTPException(422, detail=json.loads(e.json()) if hasattr(e, "json") else str(e))
    DECISIONS.parent.mkdir(exist_ok=True)
    with DECISIONS.open("a") as fh:
        fh.write(json.dumps({**d.model_dump(), "ts": time.time()}) + "\n")
    return {"ok": True}


@app.get("/api/eval")
def eval_results():
    if not EVAL.exists():
        raise HTTPException(404, "run `python -m atm.evaluate` first")
    return json.loads(EVAL.read_text())


# ---------------------------------------------------------------- v2 wave mode
MODES = ["formulas", "decision", "decision+claude"]
RATE_LIMIT, RATE_WINDOW_S = 10, 600
REPLAY_SPACING_S = 0.15
_RATE: dict[str, list[float]] = {}
_RATE_LOCK = threading.Lock()
RATE_MAX_KEYS = 10000
RUNS_MAX = 200
RUNS: OrderedDict[str, dict] = OrderedDict()  # LRU; older runs are served from disk


V2_PREFIXES = ("/api/waves", "/api/flights", "/api/wave/", "/api/negotiate-wave", "/api/runs/", "/api/benchmark")


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(V2_PREFIXES):
        return _err(422, "invalid_request", "invalid request body or parameters")
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith(V2_PREFIXES):
        codes = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request"}
        return _err(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))
    return await http_exception_handler(request, exc)


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}, **extra}, status_code=status)


def _norm_model(m: str) -> str:
    """Chain entry form: 'formula', 'provider:model'. Bare ids default to OpenRouter."""
    if m == "formula" or ":" in m:
        return m
    if m.startswith("typesafe/"):
        return "typesafe:" + m.split("/", 1)[1]
    return "openrouter:" + m


def _model_id(entry: str) -> str:
    """Config id: strip the openrouter: prefix; typesafe:x -> typesafe/x."""
    if entry.startswith("openrouter:"):
        return entry.split(":", 1)[1]
    if entry.startswith("typesafe:"):
        return "typesafe/" + entry.split(":", 1)[1]
    return entry


def _benchmark() -> dict | None:
    try:
        return json.loads(BENCHMARK.read_text())
    except (OSError, ValueError):
        return None


def _models() -> tuple[list[dict], str]:
    keys = {"openrouter": "OPENROUTER_API_KEY", "typesafe": "TYPESAFE_API_KEY"}
    bench = _benchmark() or {}
    winner = (bench.get("selection") or {}).get("winner")
    rank = {}
    for i, arm in enumerate(bench.get("arms") or []):
        rank[_model_id(_norm_model(arm["model"]))] = i + 1
    if winner:
        rank[_model_id(_norm_model(winner))] = 0
    entries = [e for e in _default_chain() if e != "formula"]
    out = []
    for e in entries:
        provider = e.split(":", 1)[0] if ":" in e else "openrouter"
        mid = _model_id(_norm_model(e))
        name = mid.split("/")[-1]
        org = mid.split("/")[0] if "/" in mid else provider
        info = {"id": mid, "provider": provider, "label": f"{org.capitalize()} {name}",
                "available": bool(os.environ.get(keys.get(provider, "")))}
        out.append(info)
    out.append({"id": "formula", "provider": "formula", "label": "Formula (deterministic)", "available": True})
    out.sort(key=lambda m: rank.get(m["id"], 1000) if m["id"] != "formula" else 10**6)
    for m in out:
        if m["id"] in rank:
            m["benchmark_rank"] = rank[m["id"]] if rank[m["id"]] else 1
    return out, _model_id(_default_chain()[0])


def _ranked_models() -> list[str]:
    """Benchmark model arms, best first: arms passing every selection gate, then by consensus rate."""
    def ok(s: dict) -> bool:
        p95 = s.get("latency_p95_ms")
        return (s.get("error_rate_pct", 100) < 2 and p95 is not None and p95 < 1000
                and s.get("consensus_rate_pct", 0) >= 90)
    arms = [a for a in (_benchmark() or {}).get("arms", []) if a.get("model") != "formula"]
    arms.sort(key=lambda a: (ok(a.get("summary", {})), a.get("summary", {}).get("consensus_rate_pct", 0)), reverse=True)
    return [f'{a.get("provider") or "openrouter"}:{a["model"]}' for a in arms if a.get("model")]


def _default_mode() -> str:
    """Follow the benchmark verdict: formulas by default when the formula agents win, else the decision models."""
    winner = ((_benchmark() or {}).get("selection") or {}).get("winner")
    return "formulas" if winner == "formula" else "decision"


def _default_chain() -> list[str]:
    """Decision-mode chain: benchmark winner (if a model), then the other benchmark models best first,
    then the remaining DEFAULT_CHAIN entries (deduped), then formula (always terminal)."""
    winner = ((_benchmark() or {}).get("selection") or {}).get("winner")
    ranked = _ranked_models()
    head = []
    if winner and winner != "formula":
        head = [e for e in ranked if e.split(":", 1)[1] == winner] or [_norm_model(winner)]
    chain: list[str] = []
    for e in head + ranked + list(decide.DEFAULT_CHAIN):
        if e != "formula" and e not in chain:
            chain.append(e)
    return chain + ["formula"]


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")  # rightmost = appended by our trusted proxy, not spoofable
    if xff and xff.split(",")[-1].strip():
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> int | None:
    """Record a live run for ip; return retry_after_s if over the limit."""
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_LIMIT:
            _RATE[ip] = hits
            return max(1, int(RATE_WINDOW_S - (now - hits[0])) + 1)
        hits.append(now)
        _RATE[ip] = hits
        if len(_RATE) > RATE_MAX_KEYS:
            for k in [k for k, v in _RATE.items() if not v or now - v[-1] >= RATE_WINDOW_S]:
                del _RATE[k]
            if len(_RATE) > RATE_MAX_KEYS:
                for k in sorted(_RATE, key=lambda k: _RATE[k][-1])[: len(_RATE) // 2]:
                    del _RATE[k]
    return None


def _save_run(result: dict) -> None:
    RUNS[result["run_id"]] = result
    RUNS.move_to_end(result["run_id"])
    while len(RUNS) > RUNS_MAX:
        RUNS.popitem(last=False)
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = RUNS_DIR / f".{result['run_id']}.{uuid.uuid4().hex[:6]}.tmp"
        tmp.write_text(json.dumps(result))
        os.replace(tmp, RUNS_DIR / f"{result['run_id']}.json")
    except OSError:
        pass


class _Cancelled(Exception):
    """Raised inside the worker when the SSE client has gone away."""


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/waves")
def waves(airport: str | None = None, q: str | None = None, limit: int = 50):
    limit = max(0, min(limit, 500))
    head = [] if (airport or q) else _replay_summaries()  # recorded flights.csv rows first: the challenge input
    return (head + WB.list_waves(airport=airport, q=q, limit=limit))[:limit]


@app.get("/api/flights")
def flights_2015(airport: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0):
    from .wave import flights2015
    rows, total = flights2015.search(airport=airport, q=q, limit=max(0, min(limit, 500)), offset=max(0, offset))
    return {"flights": rows, "total": total}


def _wx_summary(m: dict) -> str:
    if not m.get("raw"):
        return "no METAR"
    wind = f'{int(m["wind_dir_deg"]):03d}' if m.get("wind_dir_deg") is not None else "VRB"
    vis = f'{m["visibility_sm"]:g}SM' if m.get("visibility_sm") is not None else ""
    return f'{wind}{int(m.get("wind_kt") or 0):02d}KT {vis}, {m.get("surface") or "dry"}'.replace("  ", " ")


def _f2015_summary(w: dict, replay_available: bool = False) -> dict:
    flights = w["flights"]
    return {"id": w["id"], "airport": {k: w["airport"][k] for k in ("iata", "icao", "name")},
            "window_start": w["window_start"], "window_end": w["window_start"],
            "n_flights": len(flights), "n_queue": len(w["queue"]),
            "callsigns": [f["callsign"] for f in flights],
            "disrupted_callsigns": [f["callsign"] for f in flights if f.get("disrupted")],
            "weather_summary": _wx_summary(w.get("metar") or {}), "replay_available": replay_available}


_REPLAY_SUMMARIES: list[dict] | None = None


def _replay_summaries() -> list[dict]:
    """flights.csv rows with a recorded run (the demo set), built once."""
    global _REPLAY_SUMMARIES
    if _REPLAY_SUMMARIES is None:
        out = []
        for p in sorted(REPLAY_DIR.glob("F2015-*.json")) if REPLAY_DIR.is_dir() else []:
            try:
                out.append(_f2015_summary(WB.build_wave(p.stem).to_dict(), replay_available=True))
            except (KeyError, ValueError):
                continue
        _REPLAY_SUMMARIES = out
    return _REPLAY_SUMMARIES


@app.get("/api/waves/random")
def waves_random(airport: str | None = None, source: str = "flights2015"):
    """Random challenge input: a random flights.csv row (default), or a BTS 2024 wave with source=bts."""
    if source == "bts":
        try:
            return WB.random_wave(airport=airport)
        except (KeyError, IndexError):
            return _err(404, "not_found", "no waves available for that airport")
    from .wave import flights2015
    _, total = flights2015.search(airport=airport, limit=0)
    for _ in range(10):  # skip rows at airports the builder cannot model
        rows, _ = flights2015.search(airport=airport, limit=1, offset=random.randrange(total)) if total else ([], 0)
        if not rows:
            break
        try:
            w = WB.build_wave(rows[0]["wave_id"]).to_dict()
        except (KeyError, ValueError):
            continue
        return _f2015_summary(w)
    return _err(404, "not_found", "no flights.csv rows available for that airport")


@app.get("/api/wave/{wave_id}")
def get_wave(wave_id: str):
    try:
        return WB.build_wave(wave_id).to_dict()
    except KeyError:
        return _err(404, "unknown_wave", f"unknown wave id {wave_id!r}")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if run_id in RUNS:
        RUNS.move_to_end(run_id)
        return RUNS[run_id]
    if re.fullmatch(r"[0-9a-f]{12}", run_id):
        try:
            return json.loads((RUNS_DIR / f"{run_id}.json").read_text())
        except (OSError, ValueError):
            pass
    return _err(404, "unknown_run", f"unknown run id {run_id!r}")


@app.get("/api/benchmark")
def benchmark():
    b = _benchmark()
    if b is None:
        return _err(404, "not_generated", "benchmark report not generated yet")
    return b


def _model_chain(mode: str, model: str | None) -> list[str]:
    if mode == "formulas":
        return ["formula"]
    if model:
        first = _norm_model(model)
        return [first] if first == "formula" else [first, "formula"]
    return _default_chain()


@app.post("/api/negotiate-wave")
async def negotiate_wave(request: Request, body: dict = Body(...)):
    wave_id, mode = body.get("wave_id"), body.get("mode")
    model, replay_flag, stream = body.get("model"), bool(body.get("replay")), body.get("stream", True)
    if not isinstance(wave_id, str) or not wave_id:
        return _err(422, "invalid_request", "wave_id is required")
    if mode not in MODES:
        return _err(422, "invalid_request", f"mode must be one of {MODES}")
    if model is not None and not isinstance(model, str):
        return _err(422, "invalid_request", "model must be a string")
    run_id, started_at = uuid.uuid4().hex[:12], datetime.now(timezone.utc).isoformat()

    rec = None
    if replay_flag:
        try:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", wave_id):
                raise FileNotFoundError(wave_id)
            rec = replay.load(wave_id, REPLAY_DIR)
        except (FileNotFoundError, ValueError):
            rec = None  # no recording for this wave: negotiate it live instead of failing the demo
    if rec is not None:
        chain = [_model_id(rec["result"].get("model") or "formula")]
        meta = {"run_id": run_id, "wave_id": wave_id, "mode": mode, "model_chain": chain, "started_at": started_at}

        def finish_replay() -> dict:
            res = {**rec["result"], "run_id": run_id, "wave_id": wave_id, "log": rec["messages"],
                   "model": _model_id(rec["result"].get("model") or "formula")}
            _save_run(res)
            return res

        if stream is False:
            return await asyncio.to_thread(finish_replay)

        async def replay_gen():
            yield _sse("start", meta)
            for m in rec["messages"]:
                await asyncio.sleep(REPLAY_SPACING_S)
                yield _sse("message", m)
            res = await asyncio.to_thread(finish_replay)
            yield _sse("result", {k: v for k, v in res.items() if k != "log"})

        return StreamingResponse(replay_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        wave = await asyncio.to_thread(WB.build_wave, wave_id)
    except KeyError:
        return _err(404, "unknown_wave", f"unknown wave id {wave_id!r}")
    except Exception as e:  # noqa: BLE001
        return _err(500, "wave_build_failed", f"{type(e).__name__}: {e}")
    retry = _rate_limited(_client_ip(request))
    if retry is not None:
        return _err(429, "rate_limited", f"limit of {RATE_LIMIT} live negotiations per {RATE_WINDOW_S // 60} min reached",
                    retry_after_s=retry)
    chain = _model_chain(mode, model)
    meta = {"run_id": run_id, "wave_id": wave_id, "mode": mode, "model_chain": [_model_id(e) for e in chain],
            "started_at": started_at}

    def finish(res: dict, msgs: list[dict]) -> dict:
        res = {**res, "run_id": run_id, "wave_id": wave_id, "mode": mode,
               "model": _model_id(res.get("model") or chain[0]), "log": res.get("log") or msgs}
        _save_run(res)
        return res

    if stream is False:
        msgs: list[dict] = []
        try:
            res = await asyncio.to_thread(GR.negotiate_wave, wave, mode, chain, msgs.append)
        except Exception as e:  # noqa: BLE001
            return _err(500, "negotiation_failed", f"{type(e).__name__}: {e}")
        return finish(res, msgs)

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    cancel = threading.Event()

    def work() -> None:
        msgs: list[dict] = []

        def on_message(m: dict) -> None:
            if cancel.is_set():
                raise _Cancelled()
            msgs.append(m)
            loop.call_soon_threadsafe(q.put_nowait, ("message", m))

        try:
            raw = GR.negotiate_wave(wave, mode, chain, on_message)
            if cancel.is_set():
                return
            res = finish(raw, msgs)
            loop.call_soon_threadsafe(q.put_nowait, ("result", {k: v for k, v in res.items() if k != "log"}))
        except _Cancelled:
            return
        except Exception as e:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, ("error", {"code": "negotiation_failed",
                                                              "message": f"{type(e).__name__}: {e}"}))
        loop.call_soon_threadsafe(q.put_nowait, None)

    async def gen():
        try:
            yield _sse("start", meta)
            threading.Thread(target=work, daemon=True).start()
            while (item := await q.get()) is not None:
                yield _sse(*item)
        finally:
            cancel.set()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.mount("/static", StaticFiles(directory=WEB), name="static")
app.mount("/mcp", mcp_asgi_app)  # before the "/" catch-all below, or that mount would shadow it


FRONTEND_DIST = ROOT / "frontend" / "dist"

if (FRONTEND_DIST / "index.html").exists():
    # Built React frontend takes over "/" (mounted last so all /api routes keep precedence).
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")
