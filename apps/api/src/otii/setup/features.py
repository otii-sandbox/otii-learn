"""Step features: switch off LearnHouse features Otii Learn does not offer.

Uses LearnHouse's own per-organisation admin toggle, the same switch its
platform admin screen sets. Undo switches them back on.

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, OTII_LEARN_ORG_SLUG,
OTII_LEARN_DISABLED_FEATURES (comma-separated LearnHouse feature keys, e.g.
"boards"; empty switches nothing off).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.otii.setup.settings import require


def _features() -> list[str]:
    raw = os.environ.get("OTII_LEARN_DISABLED_FEATURES", "")
    return [f.strip() for f in raw.split(",") if f.strip()]


def _set_disabled(config: dict, feature: str, disabled: bool) -> None:
    from src.services.orgs.orgs import _is_v2_config

    if _is_v2_config(config):
        config.setdefault("admin_toggles", {}).setdefault(feature, {})["disabled"] = disabled
    else:
        config.setdefault("features", {}).setdefault(feature, {})["enabled"] = not disabled


async def _run(remove: bool) -> str:
    from src.db.organization_config import OrganizationConfig
    from src.db.organizations import Organization
    from src.services.orgs.orgs import _deep_copy_config

    features = _features()
    if not features:
        return "features: nothing to switch off"
    slug = require("OTII_LEARN_ORG_SLUG")
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        async with AsyncSession(engine) as session:
            org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalars().first()
            if org is None:
                raise SystemExit(f"features: no LearnHouse organisation with slug {slug}")
            row = (
                await session.execute(select(OrganizationConfig).where(OrganizationConfig.org_id == org.id))
            ).scalars().first()
            config = _deep_copy_config(row)
            for feature in features:
                _set_disabled(config, feature, not remove)
            row.config, row.update_date = config, str(datetime.now())
            session.add(row)
            await session.commit()
            return f"features: {', '.join(features)} {'back on' if remove else 'off'} for {slug}"
    finally:
        await engine.dispose()


def run(remove: bool = False) -> str:
    return asyncio.run(_run(remove))
