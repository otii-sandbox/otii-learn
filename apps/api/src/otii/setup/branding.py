"""Step branding: Otii Learn looks like otii.

Sets the organisation's LearnHouse branding the same way LearnHouse's own
settings screens do: primary colour, font, logo, and the "Made with LearnHouse"
watermark off. Undo puts LearnHouse's defaults back.

The rest of otii's look (second font, text colours, buttons, surfaces, radii)
is the stylesheet apps/web/otii/theme.css, switched on by OTII_LEARN_THEME=otii.

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, OTII_LEARN_ORG_SLUG,
OTII_LEARN_BRAND_COLOR (e.g. #1C300A), OTII_LEARN_BRAND_FONT (a Google font,
e.g. Baloo 2), OTII_LEARN_BRAND_LOGO (logo image; a relative path is read from
apps/api, so the same value works on a laptop and inside the image).
"""

from __future__ import annotations

import asyncio
import io
import mimetypes
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.datastructures import Headers, UploadFile

from src.otii.setup.settings import require


def _set_general(config: dict, key: str, value) -> None:
    from src.services.orgs.orgs import _is_v2_config

    if _is_v2_config(config):
        config.setdefault("customization", {}).setdefault("general", {})[key] = value
    else:
        config.setdefault("general", {})[key] = value


async def _run(remove: bool) -> str:
    from src.db.organization_config import OrganizationConfig
    from src.db.organizations import Organization
    from src.services.orgs.orgs import _deep_copy_config
    from src.services.orgs.uploads import upload_org_logo

    slug = require("OTII_LEARN_ORG_SLUG")
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        async with AsyncSession(engine) as session:
            org = (
                await session.execute(select(Organization).where(Organization.slug == slug))
            ).scalars().first()
            if org is None:
                raise SystemExit(f"branding: no LearnHouse organisation with slug {slug}")
            org_config = (
                await session.execute(select(OrganizationConfig).where(OrganizationConfig.org_id == org.id))
            ).scalars().first()
            if org_config is None:
                raise SystemExit(f"branding: {slug} has no configuration row")

            config = _deep_copy_config(org_config)
            if remove:
                _set_general(config, "color", "")
                _set_general(config, "font", "")
                _set_general(config, "watermark", True)
                org.logo_image = ""
            else:
                _set_general(config, "color", require("OTII_LEARN_BRAND_COLOR"))
                _set_general(config, "font", require("OTII_LEARN_BRAND_FONT"))
                _set_general(config, "watermark", False)
                logo = Path(require("OTII_LEARN_BRAND_LOGO"))
                if not logo.is_absolute():
                    logo = Path(__file__).resolve().parents[3] / logo
                content_type = mimetypes.guess_type(logo.name)[0] or "image/png"
                upload = UploadFile(
                    file=io.BytesIO(logo.read_bytes()),
                    filename=logo.name,
                    headers=Headers({"content-type": content_type}),
                )
                org.logo_image = await upload_org_logo(upload, org.org_uuid)

            org_config.config = config
            org_config.update_date = str(datetime.now())
            session.add(org_config)
            session.add(org)
            await session.commit()
            return f"branding: {'LearnHouse defaults restored' if remove else 'otii branding applied'} for {slug}"
    finally:
        await engine.dispose()


def run(remove: bool = False) -> str:
    return asyncio.run(_run(remove))
