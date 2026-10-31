"""Decision layer with a model fallback chain and a deterministic formula as the terminal entry.

Uses typesafe_sdk directly (not langchain-typesafe): TypeSafeClassifier takes only *args/**kwargs with no
documented per-call model/base_url, and we need to switch OpenRouter models and providers per chain entry.

Models only pick among bounded options; legality is decided elsewhere by the rule engine.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

_DEFAULT = ["openrouter:liquid/d1-ultra", "openrouter:baseten/elephant-1", "typesafe:jev"]
DEFAULT_CHAIN = [e.strip() for e in os.environ.get("DECISION_CHAIN", "").split(",") if e.strip()] or _DEFAULT

BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_S = 60.0
_breakers: dict[str, dict] = {}  # entry -> {"fails": int, "open_until": float}
_breakers_lock = threading.Lock()

_clients: dict[str, object] = {}  # chain entry -> TypeSafeClient (reused across calls)
_clients_lock = threading.Lock()


def _reset_breakers() -> None:
    with _breakers_lock:
        _breakers.clear()


def _reset_clients() -> None:
    with _clients_lock:
        for c in _clients.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        _clients.clear()


@dataclass
class DecisionResult:
    value: object  # str choice id | float p_yes | int score
    probabilities: dict | None
    confidence: float | None
    model: str
    provider: str  # "openrouter" | "typesafe" | "formula"
    latency_ms: int  # wall time of the whole chain run, including failed attempts and formula
    fallback_from: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempt_ms: int = 0  # time of the winning attempt only


class _Invalid(Exception):
    pass


def _stringify(state: dict) -> dict:
    return {str(k): v if isinstance(v, str) else json.dumps(v, default=str) for k, v in state.items()}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, FutureTimeout)):
        return True
    try:
        import typesafe_sdk as ts
    except ImportError:
        return False
    if isinstance(exc, (ts.TypeSafeRateLimitError, ts.TypeSafeAPITimeoutError, ts.TypeSafeInternalServerError)):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(exc, ts.TypeSafeAPIError) and isinstance(status, int) and status >= 500


class Decider:
    def __init__(self, chain: list[str] | None = None, timeout_s: float = 2.0,
                 on_fallback: Callable[[str, str, str], None] | None = None):
        self.chain = list(chain if chain is not None else DEFAULT_CHAIN)
        self.timeout_s = timeout_s
        self.on_fallback = on_fallback

    # ---- network (single choke point, monkeypatched in tests) ----
    def _client(self, entry: str):
        import typesafe_sdk as ts
        with _clients_lock:
            client = _clients.get(entry)
            if client is not None:
                return client
            provider, _, model = entry.partition(":")
            retry = ts.RetryPolicy(max_retries=0)  # the Decider owns retries
            if provider == "openrouter":
                key = os.environ.get("OPENROUTER_API_KEY")
                if not key:
                    raise RuntimeError("OPENROUTER_API_KEY not set")
                client = ts.TypeSafeClient(api_key=key, base_url="https://openrouter.ai/api", model=model,
                                           timeout=self.timeout_s, retry=retry)
            elif provider == "typesafe":
                key = os.environ.get("TYPESAFE_API_KEY")
                if not key:
                    raise RuntimeError("TYPESAFE_API_KEY not set")
                client = ts.TypeSafeClient(api_key=key, timeout=self.timeout_s, retry=retry)
            else:
                raise ValueError(f"unknown chain entry {entry!r}")
            _clients[entry] = client
            return client

    def _call(self, entry: str, state: dict, questions: dict):
        provider = entry.partition(":")[0]
        client = self._client(entry)
        kwargs = {"state": state, "questions": questions, "timeout": self.timeout_s}
        jev = os.environ.get("JEV_MODEL") if provider == "typesafe" else None
        if jev:
            kwargs["model"] = jev
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(client.system_one, **kwargs)
        try:
            return fut.result(timeout=self.timeout_s + 0.5)
        except FutureTimeout:
            raise TimeoutError(f"timeout after {self.timeout_s + 0.5:.1f}s") from None
        finally:
            ex.shutdown(wait=False)

    # ---- core ----
    def _run(self, state: dict, build_q: Callable[[], dict], parse: Callable[[object], object],
             formula: Callable[[], object]):
        """Return (parsed, entry, latency_ms, fallback_from, errors, model, attempt_ms); entry None = formula."""
        t_start = time.monotonic()
        state = _stringify(state)
        fallback_from: list[str] = []
        errors: list[str] = []
        entries = list(self.chain)
        for i, entry in enumerate(entries):
            nxt = entries[i + 1] if i + 1 < len(entries) else "formula"
            with _breakers_lock:
                br = _breakers.setdefault(entry, {"fails": 0, "open_until": 0.0})
                is_open = time.monotonic() < br["open_until"]
            err = None
            if is_open:
                err = "circuit open"
            else:
                for attempt in range(2):
                    t0 = time.monotonic()
                    try:
                        resp = self._call(entry, state, build_q())
                        parsed = parse(resp)
                        with _breakers_lock:
                            br["fails"] = 0
                        now = time.monotonic()
                        model = getattr(resp, "model", None) or entry.partition(":")[2] or entry
                        return (parsed, entry, int((now - t_start) * 1000), fallback_from, errors,
                                model, int((now - t0) * 1000))
                    except _Invalid as e:
                        err = f"invalid response: {e}"
                        break
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {e}"
                        if attempt == 0 and _is_retryable(e):
                            continue
                        break
                with _breakers_lock:
                    br["fails"] += 1
                    if br["fails"] >= BREAKER_THRESHOLD:
                        br["open_until"] = time.monotonic() + BREAKER_COOLDOWN_S
                        br["fails"] = 0
            fallback_from.append(entry)
            errors.append(f"{entry}: {err}")
            if self.on_fallback:
                self.on_fallback(entry, nxt, err)
        value = formula()
        return value, None, int((time.monotonic() - t_start) * 1000), fallback_from, errors, "formula", 0

    @staticmethod
    def _prov(entry: str | None) -> str:
        return "formula" if entry is None else entry.partition(":")[0]

    def _result(self, run, extract) -> DecisionResult:
        parsed, entry, ms, fb, errs, model, att = run
        if entry is None:
            return DecisionResult(parsed, None, None, "formula", "formula", ms, fb, errs, 0)
        value, probs, conf = extract(parsed)
        return DecisionResult(value, probs, conf, model, self._prov(entry), ms, fb, errs, att)

    # ---- public API ----
    def choice(self, state: dict, instructions: str, options: dict[str, str],
               formula: Callable[[], str]) -> DecisionResult:
        import typesafe_sdk as ts

        def parse(resp):
            a = _answer(resp, "q")
            c = getattr(a, "choice", None)
            if c not in options:
                raise _Invalid(f"choice {c!r} not in options")
            return (c, dict(getattr(a, "probabilities", None) or {}) or None, getattr(a, "confidence", None))

        run = self._run(state, lambda: {"q": ts.Choice(instructions=instructions, criteria=dict(options))},
                        parse, formula)
        return self._result(run, lambda p: p)

    def noul(self, state: dict, instructions: str, formula: Callable[[], float]) -> DecisionResult:
        import typesafe_sdk as ts

        def parse(resp):
            return (_p_yes(_answer(resp, "q")), None, None)

        run = self._run(state, lambda: {"q": ts.Noul(instructions=instructions)}, parse, formula)
        return self._result(run, lambda p: p)

    def noul_batch(self, state: dict, items: dict[str, str],
                   formula: Callable[[str], float]) -> dict[str, DecisionResult]:
        import typesafe_sdk as ts
        ids = list(items)
        if not ids:
            return {}

        def parse(resp):
            return {iid: _p_yes(_answer(resp, f"q{i}")) for i, iid in enumerate(ids)}

        run = self._run(state, lambda: {f"q{i}": ts.Noul(instructions=items[iid]) for i, iid in enumerate(ids)},
                        parse, lambda: {iid: formula(iid) for iid in ids})
        parsed, entry, ms, fb, errs, model, att = run
        if entry is None:
            return {iid: DecisionResult(parsed[iid], None, None, "formula", "formula", ms, list(fb), list(errs), 0)
                    for iid in ids}
        return {iid: DecisionResult(parsed[iid], None, None, model, self._prov(entry), ms, list(fb), list(errs),
                                    att) for iid in ids}

    def score(self, state: dict, instructions: str, levels: list[str],
              formula: Callable[[], int]) -> DecisionResult:
        import typesafe_sdk as ts

        def parse(resp):
            a = _answer(resp, "q")
            raw = getattr(a, "score", None)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
                raise _Invalid(f"score {raw!r} not a number")
            s = int(round(raw))
            if s not in range(len(levels)):
                raise _Invalid(f"score {s} out of range")
            return (s, getattr(a, "probabilities", None), getattr(a, "confidence", None))

        run = self._run(state, lambda: {"q": ts.Score(instructions=instructions, criteria=list(levels))},
                        parse, formula)
        return self._result(run, lambda p: p)


def _answer(resp, key: str):
    answers = getattr(resp, "answers", None)
    try:
        a = answers[key]
    except (KeyError, TypeError, IndexError):
        raise _Invalid(f"missing answer {key!r}") from None
    if a is None:
        raise _Invalid(f"missing answer {key!r}")
    return a


def _p_yes(a) -> float:
    v = getattr(a, "noul", None)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
        raise _Invalid(f"noul {v!r} not in [0,1]")
    return float(v)
