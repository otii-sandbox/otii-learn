"""Tiny Northflank API helper for Otii Learn's scripts. Uses the CLI login or NF_API_TOKEN; never prints secrets."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.northflank.com/v1"
_TEAM = ""


def use_team(env: str) -> dict:
    """Load <env>-arguments.json and send every call through its Northflank team.

    The login is organisation-wide since 1 Oct 2026: Northflank refuses any
    call without the team in its address (HTTP 403)."""
    global _TEAM
    arguments = json.loads((Path(__file__).resolve().parent / f"{env}-arguments.json").read_text())
    if not arguments.get("team"):
        die(f"{env}-arguments.json has no team (the Northflank team id that owns the project)")
    _TEAM = arguments["team"]
    return arguments


def die(message: str) -> None:
    print(f"STOP: {message}", file=sys.stderr)
    sys.exit(1)


def token() -> str:
    value = os.environ.get("NF_API_TOKEN", "")
    if value:
        return value
    cfg = json.loads(Path("~/.northflank/config.json").expanduser().read_text())
    ctx = next((c for c in cfg.get("contexts", []) if c.get("name") == cfg.get("current")), None)
    if not ctx or not ctx.get("token"):
        die("no Northflank token: export NF_API_TOKEN or log in with the CLI")
    return ctx["token"]


def call(tok: str, method: str, path: str, body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    if not _TEAM:
        die("no Northflank team set: call nf.use_team(<environment>) first")
    req = urllib.request.Request(f"{API}/teams/{_TEAM}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw[:500].decode(errors="replace")}


def unwrap(body: dict) -> dict:
    return body.get("data", body) if isinstance(body, dict) else body
