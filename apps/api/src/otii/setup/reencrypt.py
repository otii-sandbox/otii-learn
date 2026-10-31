"""Step reencrypt: move every value stored under a previous JWT key onto the current one.

Run after a key rotation (src/otii/key_ring.py):
    python -m src.otii.setup reencrypt           # dry run: counts only, writes nothing
    python -m src.otii.setup reencrypt --apply   # re-encrypts what is on a previous key

Encrypted columns (read under any key of the ring, written under the current key):
- webhook_endpoint.secret_encrypted  webhook signing secrets (services/webhooks/crypto.py)
- usermfa.secret_encrypted           2FA (TOTP) secrets (services/auth/mfa.py)

Counted only, they cannot be moved (they are one-way hashes of a value the
database never holds):
- usermfabackupcode.code_hash  unused 2FA backup codes (peppered with the JWT key)
- apitoken.token_hash and superadmin_apitoken.token_hash  active API tokens
  (peppered with the JWT key; re-hashed under the current key at their next use)

Each write is compare-and-set: the row is updated only if it still holds the
value that was read, so a value the app changed meanwhile is left alone (and
counted). Values are never printed. Exit status is non-zero when any value
opens under no key of the ring. Safe to rerun: values already on the current
key are not touched.

No undo is needed: the old values open under the old key, so to go back put
the old key in LEARNHOUSE_AUTH_JWT_SECRET_KEY, the new one in
LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS, and run this step again.

Values no key opens (made under a key that is gone) stop the step. When each is
explained and accepted as lost, clear them once with CLEAR_UNREADABLE=yes on
an --apply run: each becomes "" (what the column holds when nothing is stored),
compare-and-set like a move, and its row id is printed so it can be put back from
the database backup taken before the run. Nothing changes for a user: an empty
and an unreadable secret are already treated alike (2FA: "broken", never "not
enrolled"; webhooks: the delivery is skipped with a warning).

Not in `all`: it is meant to run once per rotation, by hand, after the new key
is live; and a value no key opens must stop it loudly, which inside `all`
would stop every release job.

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, LEARNHOUSE_AUTH_JWT_SECRET_KEY,
LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS (and LEARNHOUSE_MFA_ENCRYPTION_KEY if set).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.otii.setup.settings import require


@dataclass
class Column:
    """One encrypted column and the keys it may be under (current first)."""

    name: str
    table: object
    column: str
    ring: Callable[[], list[Fernet]]


@dataclass
class Counts:
    current: int = 0
    moved: int = 0  # dry run: would move
    changed_meanwhile: int = 0
    unreadable: int = 0
    empty: int = 0
    cleared: int = 0
    pending: list = field(default_factory=list)  # (id, old value, plaintext); never printed
    lost: list = field(default_factory=list)  # (id, old value) no key opens; never printed

    def line(self, name: str, apply: bool) -> str:
        verb = "moved to the current key" if apply else "on a previous key (would move)"
        parts = [f"{self.current} on the current key", f"{self.moved} {verb}"]
        if apply:
            parts.append(f"{self.changed_meanwhile} changed by the app meanwhile, left alone")
        parts += [f"{self.unreadable} open under no key", f"{self.empty} empty"]
        if self.cleared:
            parts.append(f"{self.cleared} of those cleared")
        return f"  {name}: " + ", ".join(parts)


def columns() -> list[Column]:
    from src.db.user_mfa import UserMFA
    from src.db.webhooks import WebhookEndpoint
    from src.services.auth.mfa import mfa_fernet_ring
    from src.services.webhooks.crypto import webhook_fernet_ring

    return [
        Column("webhook_endpoint.secret_encrypted", WebhookEndpoint, "secret_encrypted", webhook_fernet_ring),
        Column(f"{UserMFA.__tablename__}.secret_encrypted", UserMFA, "secret_encrypted", mfa_fernet_ring),
    ]


def _open(ring: list[Fernet], value: str) -> tuple[int | None, bytes | None]:
    """Index of the first key that opens ``value`` and the plaintext, or (None, None)."""
    for index, key in enumerate(ring):
        try:
            return index, key.decrypt(value.encode())
        except (InvalidToken, ValueError, TypeError):
            continue
    return None, None


async def scan(engine: AsyncEngine, cols: list[Column]) -> dict[str, Counts]:
    """Read every value once and sort it by the key that opens it."""
    result: dict[str, Counts] = {}
    async with engine.connect() as conn:
        for col in cols:
            table = col.table.__table__
            counts = Counts()
            rows = (await conn.execute(select(table.c.id, table.c[col.column]))).all()
            ring = col.ring()
            for row_id, value in rows:
                if not value:
                    counts.empty += 1
                    continue
                index, plain = _open(ring, value)
                if index is None:
                    counts.unreadable += 1
                    counts.lost.append((row_id, value))
                elif index == 0:
                    counts.current += 1
                else:
                    counts.moved += 1
                    counts.pending.append((row_id, value, plain))
            result[col.name] = counts
    return result


async def write(engine: AsyncEngine, cols: list[Column], found: dict[str, Counts], clear: bool = False) -> None:
    """Re-encrypt each pending value under the current key, compare-and-set, one transaction per row.
    With ``clear``, each value no key opens becomes "" the same way."""
    for col in cols:
        table = col.table.__table__
        counts = found[col.name]
        current = col.ring()[0]
        moved = 0
        for row_id, old, plain in counts.pending:
            new = current.encrypt(plain).decode()
            async with engine.begin() as conn:
                hit = await conn.execute(
                    update(table)
                    .where(table.c.id == row_id, table.c[col.column] == old)
                    .values({col.column: new})
                )
            if hit.rowcount == 1:
                moved += 1
            else:
                counts.changed_meanwhile += 1
        counts.moved = moved
        counts.pending = []
        if not clear:
            continue
        for row_id, old in counts.lost:
            async with engine.begin() as conn:
                hit = await conn.execute(
                    update(table).where(table.c.id == row_id, table.c[col.column] == old).values({col.column: ""})
                )
            if hit.rowcount == 1:
                counts.cleared += 1
                print(f"cleared unreadable {col.name} id={row_id}")
            else:
                counts.changed_meanwhile += 1
        counts.lost = []


async def hash_counts(engine: AsyncEngine) -> list[str]:
    """Hashed values tied to the JWT key, counted only."""
    from src.db.api_tokens import APIToken
    from src.db.superadmin_api_tokens import SuperadminAPIToken
    from src.db.user_mfa import UserMFABackupCode

    codes = UserMFABackupCode.__table__
    lines = []
    async with engine.connect() as conn:
        unused, users = (
            await conn.execute(
                select(func.count(), func.count(func.distinct(codes.c.user_id))).where(codes.c.used_at.is_(None))
            )
        ).one()
        lines.append(
            f"  {codes.name}.code_hash: {unused} unused backup codes of {users} people. Cannot be moved "
            "(hashes); a code issued before the rotation stops working when the previous key is removed, "
            "so those people regenerate their codes"
        )
        for model in (APIToken, SuperadminAPIToken):
            table = model.__table__
            active = (await conn.execute(select(func.count()).where(table.c.is_active.is_(True)))).scalar_one()
            lines.append(
                f"  {table.name}.token_hash: {active} active. Cannot be moved (hashes); each is re-hashed "
                "under the current key at its next use, an unused one stops working when the previous key is removed"
            )
    return lines


async def reencrypt(
    engine: AsyncEngine, apply: bool, cols: list[Column] | None = None, clear: bool = False
) -> tuple[str, int]:
    """Returns (report, number of values no key opens and not cleared). Never includes a value in the report."""
    from src.security.security import PREVIOUS_SECRET_KEYS

    cols = cols if cols is not None else columns()
    found = await scan(engine, cols)
    if apply:
        await write(engine, cols, found, clear=clear)
    unreadable = sum(c.unreadable - c.cleared for c in found.values())
    head = "reencrypt: " + ("applied" if apply else "dry run, nothing written (add --apply to write)")
    lines = [head, f"  previous keys in the ring: {len(PREVIOUS_SECRET_KEYS)}"]
    lines += [found[c.name].line(c.name, apply) for c in cols]
    lines += await hash_counts(engine)
    if unreadable:
        lines.append(
            f"reencrypt: {unreadable} values open under no key. Put the key they were made with in "
            "LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS and rerun"
        )
    return "\n".join(lines), unreadable


async def _run(apply: bool) -> tuple[str, int]:
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        return await reencrypt(engine, apply, clear=clear_requested())
    finally:
        await engine.dispose()


def clear_requested() -> bool:
    return os.environ.get("CLEAR_UNREADABLE", "").strip().lower() == "yes"


def run(remove: bool = False, apply: bool = False) -> str:
    if clear_requested() and not apply:
        raise SystemExit("reencrypt: CLEAR_UNREADABLE=yes only works with --apply (a dry run already counts them)")
    if remove:
        return ("reencrypt: no undo needed; to go back swap the two key settings "
                "(old key current, new key previous) and run reencrypt --apply again")
    report, unreadable = asyncio.run(_run(apply))
    if unreadable:
        print(report)
        raise SystemExit(1)
    return report
