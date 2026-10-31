"""Import atm.wave.<name>, falling back to atm.wave.stub only if that module itself is missing."""
from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger(__name__)


def _load(name: str):
    try:
        return importlib.import_module(f"atm.wave.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"atm.wave.{name}":
            raise  # a dependency of the real module is broken: surface it, never hide behind the stub
        if os.environ.get("ATM_REQUIRE_REAL") == "1":
            raise
        log.warning("atm.wave.%s not found; using atm.wave.stub", name)
        return importlib.import_module("atm.wave.stub")
