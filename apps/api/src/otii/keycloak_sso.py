"""Sign in to Otii Learn with otii's Keycloak (OpenID Connect, code flow + PKCE).

Serves the /auth/sso/* routes LearnHouse's web app already calls
(apps/web/services/auth/sso.ts, apps/web/app/auth/sso/callback/page.tsx),
plus GET /auth/sso/login which redirects straight to Keycloak so otii can open
Otii Learn without showing LearnHouse's login page.

Identity: people are matched on Keycloak's `sub`, stored in
User.extra_metadata["keycloak_sub"]. An existing LearnHouse account with the
same email is linked on first Keycloak sign-in; an account already linked to a
different `sub` is refused. New people are created on first sign-in.

Settings (all required for the routes to be active):
  OTII_KEYCLOAK_URL            browser-facing Keycloak base, e.g. https://auth.get-otii.com
  OTII_KEYCLOAK_INTERNAL_URL   how this API reaches Keycloak (defaults to OTII_KEYCLOAK_URL)
  OTII_KEYCLOAK_REALM          realm name
  OTII_KEYCLOAK_CLIENT_ID      confidential client for Otii Learn
  OTII_KEYCLOAK_CLIENT_SECRET  its secret
  OTII_LEARN_PUBLIC_URL        Otii Learn web address, e.g. https://learn.get-otii.com
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, User, UserCreate, UserRead
from src.security.auth import get_current_user
from src.security.session_context import AUTH_METHOD_SSO
from src.services.auth.session import mint_session_tokens
from src.services.security.rate_limiting import get_redis_connection

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600
STATE_KEY = "otii:kc_sso_state:{state}"
LEARNER_ROLE_ID = 4  # LearnHouse's seeded "User" role


@dataclass(frozen=True)
class KeycloakSettings:
    public_url: str
    internal_url: str
    realm: str
    client_id: str
    client_secret: str
    learn_public_url: str

    @property
    def issuer(self) -> str:
        return f"{self.public_url}/realms/{self.realm}"

    def internal(self, path: str) -> str:
        return f"{self.internal_url}/realms/{self.realm}/protocol/openid-connect/{path}"

    @property
    def redirect_uri(self) -> str:
        return f"{self.learn_public_url}/auth/sso/callback"


def load_settings() -> Optional[KeycloakSettings]:
    public = os.environ.get("OTII_KEYCLOAK_URL", "").rstrip("/")
    realm = os.environ.get("OTII_KEYCLOAK_REALM", "")
    client_id = os.environ.get("OTII_KEYCLOAK_CLIENT_ID", "")
    secret = os.environ.get("OTII_KEYCLOAK_CLIENT_SECRET", "")
    learn = os.environ.get("OTII_LEARN_PUBLIC_URL", "").rstrip("/")
    if not all([public, realm, client_id, secret, learn]):
        return None
    internal = os.environ.get("OTII_KEYCLOAK_INTERNAL_URL", "").rstrip("/") or public
    return KeycloakSettings(public, internal, realm, client_id, secret, learn)


def sso_error(status_code: int, code: str, description: str) -> HTTPException:
    # Shape the web app's SSOError reads (apps/web/services/auth/sso.ts).
    return HTTPException(
        status_code=status_code,
        detail={
            "error": "sso_error",
            "error_code": code,
            "error_description": description,
            "provider": "keycloak",
        },
    )


def require_settings() -> KeycloakSettings:
    settings = load_settings()
    if settings is None:
        raise sso_error(404, "sso_not_configured", "Keycloak sign-in is not configured")
    return settings


def safe_next_path(next_path: Optional[str]) -> str:
    """Only same-site relative paths; anything else lands on the home page."""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    if urlparse(next_path).netloc:
        return "/"
    return next_path


async def resolve_org(db_session: AsyncSession, org_slug: str) -> Organization:
    org = (
        await db_session.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalars().first()
    if org is None:
        raise sso_error(404, "org_not_found", "Organization not found")
    return org


def start_login(settings: KeycloakSettings, org_slug: str, next_path: str) -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(24)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    get_redis_connection().setex(
        STATE_KEY.format(state=state),
        STATE_TTL_SECONDS,
        json.dumps({"verifier": verifier, "nonce": nonce, "org_slug": org_slug, "next": next_path}),
    )
    query = urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": settings.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{settings.issuer}/protocol/openid-connect/auth?{query}", state


def take_state(state: str) -> dict:
    raw = get_redis_connection().getdel(STATE_KEY.format(state=state))
    if not raw:
        raise sso_error(400, "invalid_state", "This sign-in link has expired. Please try again.")
    return json.loads(raw)


async def exchange_code(settings: KeycloakSettings, code: str, verifier: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.internal("token"),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.redirect_uri,
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "code_verifier": verifier,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("Keycloak token endpoint unreachable: %s", exc)
        raise sso_error(502, "idp_unreachable", "Could not reach the sign-in service")
    if response.status_code != 200:
        logger.warning("Keycloak refused the code: %s", response.status_code)
        raise sso_error(401, "code_exchange_failed", "Sign-in could not be completed")
    return response.json()


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def verify_id_token(settings: KeycloakSettings, id_token: str, nonce: str) -> dict:
    jwks_url = settings.internal("certs")
    client = _jwks_clients.setdefault(jwks_url, jwt.PyJWKClient(jwks_url))
    try:
        key = client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=settings.client_id,
            issuer=settings.issuer,
        )
    except jwt.PyJWTError as exc:
        logger.warning("Keycloak id_token rejected: %s", exc)
        raise sso_error(401, "invalid_id_token", "Sign-in could not be verified")
    if claims.get("nonce") != nonce:
        raise sso_error(401, "invalid_nonce", "Sign-in could not be verified")
    if not claims.get("sub") or not claims.get("email"):
        raise sso_error(401, "missing_claims", "Your account has no email address")
    if claims.get("email_verified") is False:
        raise sso_error(403, "email_not_verified", "Please verify your email address first")
    return claims


async def find_or_create_user(
    request: Request,
    db_session: AsyncSession,
    current_user,
    claims: dict,
    org: Organization,
) -> User:
    sub = claims["sub"]
    email = claims["email"].strip().lower()

    user = (
        await db_session.execute(
            select(User).where(User.extra_metadata["keycloak_sub"].astext == sub)
        )
    ).scalars().first()

    if user is None:
        user = (
            await db_session.execute(select(User).where(func.lower(User.email) == email))
        ).scalars().first()
        if user is not None:
            linked = (user.extra_metadata or {}).get("keycloak_sub")
            if linked and linked != sub:
                raise sso_error(409, "account_conflict", "This email belongs to another account")

    if user is None:
        from src.services.users.users import create_user

        local = email.split("@")[0] or "learner"
        created = await create_user(
            request,
            db_session,
            current_user,
            UserCreate(
                email=email,
                username=f"{local}{secrets.randbelow(900000) + 100000}",
                password="",
                first_name=claims.get("given_name", ""),
                last_name=claims.get("family_name", ""),
            ),
            org.id,
            is_oauth=True,
            signup_provider="keycloak",
        )
        user = (
            await db_session.execute(select(User).where(User.id == created.id))
        ).scalars().first()

    changed = False
    metadata = dict(user.extra_metadata or {})
    if metadata.get("keycloak_sub") != sub:
        metadata["keycloak_sub"] = sub
        user.extra_metadata = metadata
        changed = True
    if user.email.lower() != email:
        user.email = email
        changed = True
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = datetime.now(timezone.utc).isoformat()
        changed = True
    if changed:
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

    membership = (
        await db_session.execute(
            select(UserOrganization).where(
                (UserOrganization.user_id == user.id) & (UserOrganization.org_id == org.id)
            )
        )
    ).scalars().first()
    if membership is None:
        now = str(datetime.now())
        db_session.add(
            UserOrganization(
                user_id=user.id,
                org_id=org.id,
                role_id=LEARNER_ROLE_ID,
                creation_date=now,
                update_date=now,
            )
        )
        await db_session.commit()
    return user


router = APIRouter()


@router.get("/check")
async def sso_check(org_slug: str):
    enabled = load_settings() is not None
    return {"sso_enabled": enabled, "provider": "keycloak" if enabled else None}


@router.get("/authorize")
async def sso_authorize(
    org_slug: str,
    next: Optional[str] = None,
    db_session: AsyncSession = Depends(get_db_session),
):
    settings = require_settings()
    await resolve_org(db_session, org_slug)
    url, state = start_login(settings, org_slug, safe_next_path(next))
    return {"authorization_url": url, "state": state}


@router.get("/login")
async def sso_login(
    org_slug: str,
    next: Optional[str] = None,
    db_session: AsyncSession = Depends(get_db_session),
):
    """Straight to Keycloak. otii links here so nobody sees a second login page."""
    settings = require_settings()
    await resolve_org(db_session, org_slug)
    url, _ = start_login(settings, org_slug, safe_next_path(next))
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
async def sso_callback(
    request: Request,
    response: Response,
    code: str,
    state: str,
    current_user: AnonymousUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    from src.routers.auth import set_auth_cookies

    settings = require_settings()
    saved = take_state(state)
    tokens = await exchange_code(settings, code, saved["verifier"])
    claims = verify_id_token(settings, tokens.get("id_token", ""), saved["nonce"])
    org = await resolve_org(db_session, saved["org_slug"])
    user = await find_or_create_user(request, db_session, current_user, claims, org)

    issued = mint_session_tokens(user.email, amr=AUTH_METHOD_SSO, org_id=org.id)
    set_auth_cookies(response, issued.access_token, issued.refresh_token, request)
    return {
        "user": UserRead.model_validate(user).model_dump(mode="json"),
        "tokens": {
            "access_token": issued.access_token,
            "refresh_token": issued.refresh_token,
            "expiry": None,
        },
        "redirect_url": f"{settings.learn_public_url}{saved['next']}",
        "org_slug": org.slug,
    }
