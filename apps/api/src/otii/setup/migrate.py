"""Step migrate: bring Otii Learn's database schema up to this image's version.

Same order and rule as LearnHouse's own updater (apps/cli/src/commands/update-ee.ts):
the new API starts first (it creates any missing tables), then this step runs.
A database never tracked by Alembic is stamped at this image's heads; a tracked
one gets `alembic upgrade head`.

No undo: schema changes are not rolled back. To go back, restore the database
from the backup taken before the release.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[3]  # apps/api


def _alembic(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"migrate: alembic {' '.join(args)} failed:\n{result.stderr[-2000:]}")
    return result.stdout


def run(remove: bool = False) -> str:
    if remove:
        return "migrate: no undo; restore the pre-release backup to go back"
    current = {m.group(1) for m in re.finditer(r"^([0-9a-z]{8,40})\b", _alembic("current"), re.M)}
    if not current:
        _alembic("stamp", "heads")
        return "migrate: new database stamped at this version"
    _alembic("upgrade", "head")
    return "migrate: upgraded to head"
