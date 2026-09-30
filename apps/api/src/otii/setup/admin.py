"""Step admin: the built-in admin's password is the one in the settings.

LearnHouse sets that password once, at install. Northflank now keeps the
setting stable across releases, but before 30 Sep 2026 every release drew a
new one, so the stored value no longer opened the account. This step makes
them agree on every run. Undo: nothing to restore (the old value is gone).

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, LEARNHOUSE_INITIAL_ADMIN_EMAIL,
LEARNHOUSE_INITIAL_ADMIN_PASSWORD.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.otii.setup.settings import require


async def _run(remove: bool) -> str:
    from src.db.users import User
    from src.security.security import security_hash_password, security_verify_password

    if remove:
        return "admin: no undo; the password stays as set"
    email = require("LEARNHOUSE_INITIAL_ADMIN_EMAIL")
    password = require("LEARNHOUSE_INITIAL_ADMIN_PASSWORD")
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        async with AsyncSession(engine) as session:
            user = (await session.execute(select(User).where(User.email == email))).scalars().first()
            if user is None:
                raise SystemExit(f"admin: no Learn account with the address in LEARNHOUSE_INITIAL_ADMIN_EMAIL")
            if user.password and security_verify_password(password, user.password):
                return "admin: password already matches the settings"
            user.password = security_hash_password(password)
            user.update_date = str(datetime.now())
            session.add(user)
            await session.commit()
            return "admin: password set from the settings"
    finally:
        await engine.dispose()


def run(remove: bool = False) -> str:
    return asyncio.run(_run(remove))
