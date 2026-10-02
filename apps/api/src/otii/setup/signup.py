"""Step signup: nobody can create an Otii Learn account on its own sign-up page.

People reach Otii Learn through otii's sign-in, which creates their account.
LearnHouse's public sign-up page would let anyone on the internet register
with any email. This sets the organisation to invite only, the same per-
organisation switch LearnHouse's own admin screen sets. otii's sign-in is not
affected: the rule is enforced on the public sign-up route, not on the account
creation otii's sign-in uses. Undo sets it back to open.

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, OTII_LEARN_ORG_SLUG.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.otii.setup.settings import require

INVITE_ONLY, OPEN = "inviteOnly", "open"


def set_signup_mode(config: dict, mode: str) -> None:
    from src.services.orgs.orgs import _is_v2_config

    if _is_v2_config(config):
        config.setdefault("admin_toggles", {}).setdefault("members", {})["signup_mode"] = mode
    else:
        config.setdefault("features", {}).setdefault(
            "members", {"enabled": True, "signup_mode": OPEN, "admin_limit": 1, "limit": 10}
        )["signup_mode"] = mode


async def _run(remove: bool) -> str:
    from src.db.organization_config import OrganizationConfig
    from src.db.organizations import Organization
    from src.services.orgs.orgs import _deep_copy_config

    mode = OPEN if remove else INVITE_ONLY
    slug = require("OTII_LEARN_ORG_SLUG")
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        async with AsyncSession(engine) as session:
            org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalars().first()
            if org is None:
                raise SystemExit(f"signup: no LearnHouse organisation with slug {slug}")
            row = (
                await session.execute(select(OrganizationConfig).where(OrganizationConfig.org_id == org.id))
            ).scalars().first()
            config = _deep_copy_config(row)
            set_signup_mode(config, mode)
            row.config, row.update_date = config, str(datetime.now())
            session.add(row)
            await session.commit()
            return f"signup: {slug} is {'open to anyone' if remove else 'invite only (public sign-up closed)'}"
    finally:
        await engine.dispose()


def run(remove: bool = False) -> str:
    return asyncio.run(_run(remove))
