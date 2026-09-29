"""Internal hosts Otii Learn may send webhooks to.

LearnHouse refuses webhook URLs on private addresses, which is right when any
organisation admin can type a URL. otii's backend sits on the same private
network as Otii Learn, so it is named here explicitly.

OTII_TRUSTED_WEBHOOK_HOSTS: comma-separated exact hostnames, e.g. "backend".
Unset (the default) trusts nothing and leaves LearnHouse's guard as it was.
"""

from __future__ import annotations

import os


def trusted_webhook_hosts() -> frozenset[str]:
    raw = os.environ.get("OTII_TRUSTED_WEBHOOK_HOSTS", "")
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def is_trusted_webhook_host(hostname: str | None) -> bool:
    return bool(hostname) and hostname.lower() in trusted_webhook_hosts()
