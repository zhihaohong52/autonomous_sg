"""Load KEY=VALUE pairs from the repo's .env (gitignored) without overriding the real environment."""
import os
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _k, _, _v = _line.strip().partition("=")
        if _k and not _k.startswith("#") and _v:
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
