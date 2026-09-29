"""Step webhook: LearnHouse tells otii about every finished course.

One webhook per Otii Learn organisation, marked source="otii" so this step
only ever touches its own. The signing secret comes from settings
(OTII_LEARN_WEBHOOK_SECRET), the same value otii checks signatures with, so
nothing is copied between systems by hand.

Settings: LEARNHOUSE_SQL_CONNECTION_STRING, LEARNHOUSE_AUTH_JWT_SECRET_KEY
(LearnHouse encrypts stored secrets with it), OTII_LEARN_ORG_SLUG,
OTII_LEARN_WEBHOOK_URL, OTII_LEARN_WEBHOOK_SECRET.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.otii.setup.settings import require

SOURCE = "otii"
EVENTS = ["course_completed"]
ADMIN_ROLE_ID = 1  # LearnHouse's seeded Admin role


async def _run(remove: bool) -> str:
    from src.db.organizations import Organization
    from src.db.user_organizations import UserOrganization
    from src.db.webhooks import WebhookEndpoint
    from src.services.webhooks.crypto import encrypt_secret

    slug = require("OTII_LEARN_ORG_SLUG")
    engine = create_async_engine(require("LEARNHOUSE_SQL_CONNECTION_STRING"))
    try:
        async with AsyncSession(engine) as session:
            org = (
                await session.execute(select(Organization).where(Organization.slug == slug))
            ).scalars().first()
            if org is None:
                raise SystemExit(f"webhook: no LearnHouse organisation with slug {slug}")

            existing = (
                await session.execute(
                    select(WebhookEndpoint).where(
                        WebhookEndpoint.org_id == org.id, WebhookEndpoint.source == SOURCE
                    )
                )
            ).scalars().all()

            if remove:
                for endpoint in existing:
                    await session.delete(endpoint)
                await session.commit()
                return f"webhook: removed {len(existing)} from {slug}"

            url = require("OTII_LEARN_WEBHOOK_URL")
            secret = encrypt_secret(require("OTII_LEARN_WEBHOOK_SECRET"))
            now = str(datetime.now())
            if existing:
                endpoint = existing[0]
                endpoint.url, endpoint.secret_encrypted = url, secret
                endpoint.events, endpoint.is_active, endpoint.update_date = EVENTS, True, now
                for extra in existing[1:]:
                    await session.delete(extra)
                verb = "updated"
            else:
                admin = (
                    await session.execute(
                        select(UserOrganization).where(
                            UserOrganization.org_id == org.id,
                            UserOrganization.role_id == ADMIN_ROLE_ID,
                        )
                    )
                ).scalars().first()
                if admin is None:
                    raise SystemExit(f"webhook: {slug} has no admin to own the webhook")
                endpoint = WebhookEndpoint(
                    webhook_uuid=f"webhook_{uuid4()}",
                    org_id=org.id,
                    url=url,
                    secret_encrypted=secret,
                    description="otii: Otii Learn log",
                    events=EVENTS,
                    is_active=True,
                    source=SOURCE,
                    created_by_user_id=admin.user_id,
                    creation_date=now,
                    update_date=now,
                )
                verb = "created"
            session.add(endpoint)
            await session.commit()
            return f"webhook: {verb} for {slug} -> {url}"
    finally:
        await engine.dispose()


def run(remove: bool = False) -> str:
    return asyncio.run(_run(remove))
