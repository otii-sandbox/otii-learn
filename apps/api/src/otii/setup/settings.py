"""Read setup settings from the environment. Missing required settings stop the step."""

from __future__ import annotations

import os


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing setting {name}")
    return value
