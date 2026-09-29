"""Create or update the Otii Learn client in otii's Keycloak realm. Safe to rerun.

The client is confidential (LearnHouse's API exchanges the code server-side),
uses PKCE, and gets no otii-specific claims: in particular no otii_user_id, so
its tokens are never accepted by otii's own API.

Usage (env): KEYCLOAK_ADMIN_URL, KEYCLOAK_ADMIN_USERNAME, KEYCLOAK_ADMIN_PASSWORD,
             OTII_KEYCLOAK_REALM, OTII_KEYCLOAK_CLIENT_ID, OTII_LEARN_PUBLIC_URL
    python -m src.otii.keycloak_client [--write-secret PATH]

--write-secret stores the client secret as OTII_KEYCLOAK_CLIENT_SECRET in an
env file (created or updated in place, mode 600). The secret is never printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

# Clients this script must never touch.
PROTECTED_CLIENT_IDS = {
    "otii-frontend",
    "otii-backend",
    "otii-pipeline",
    "admin-cli",
    "account",
    "account-console",
    "broker",
    "realm-management",
    "security-admin-console",
}


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing {name}")
    return value


def admin_token(client: httpx.Client, base: str) -> str:
    response = client.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": env("KEYCLOAK_ADMIN_USERNAME"),
            "password": env("KEYCLOAK_ADMIN_PASSWORD"),
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def client_body(client_id: str, learn_url: str) -> dict:
    return {
        "clientId": client_id,
        "name": "Otii Learn",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "frontchannelLogout": True,
        "redirectUris": [f"{learn_url}/auth/sso/callback"],
        "webOrigins": [learn_url],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": f"{learn_url}/*",
        },
    }


def write_secret(path: Path, secret: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith("OTII_KEYCLOAK_CLIENT_SECRET=")]
    lines.append(f"OTII_KEYCLOAK_CLIENT_SECRET={secret}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def main(argv: list[str]) -> None:
    base = env("KEYCLOAK_ADMIN_URL").rstrip("/")
    realm = env("OTII_KEYCLOAK_REALM")
    client_id = env("OTII_KEYCLOAK_CLIENT_ID")
    learn_url = env("OTII_LEARN_PUBLIC_URL").rstrip("/")
    if client_id in PROTECTED_CLIENT_IDS:
        sys.exit(f"refusing to manage protected client {client_id}")

    with httpx.Client(timeout=15) as http:
        headers = {"Authorization": f"Bearer {admin_token(http, base)}"}
        clients_url = f"{base}/admin/realms/{realm}/clients"
        found = http.get(clients_url, params={"clientId": client_id}, headers=headers)
        found.raise_for_status()
        body = client_body(client_id, learn_url)
        if found.json():
            internal_id = found.json()[0]["id"]
            http.put(f"{clients_url}/{internal_id}", json=body, headers=headers).raise_for_status()
            print(f"updated client {client_id} in realm {realm}")
        else:
            http.post(clients_url, json=body, headers=headers).raise_for_status()
            internal_id = http.get(clients_url, params={"clientId": client_id}, headers=headers).json()[0]["id"]
            print(f"created client {client_id} in realm {realm}")

        if "--write-secret" in argv:
            target = Path(argv[argv.index("--write-secret") + 1])
            secret = http.get(f"{clients_url}/{internal_id}/client-secret", headers=headers)
            secret.raise_for_status()
            write_secret(target, secret.json()["value"])
            print(f"client secret written to {target}")


if __name__ == "__main__":
    main(sys.argv[1:])
