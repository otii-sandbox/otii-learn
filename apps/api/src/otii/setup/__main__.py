"""python -m src.otii.setup {all|database|migrate|admin|webhook|branding|features|signup|reencrypt} [--remove]

`all` runs the steps that need a running Otii Learn: migrate, admin, webhook,
branding, features, signup. `database` runs before the API exists, on its own.
`reencrypt` runs on its own after a JWT key rotation: a dry run (counts only)
unless `--apply` is given. See src/otii/setup/reencrypt.py.
"""

from __future__ import annotations

import sys

from src.otii.setup import admin, branding, database, features, migrate, reencrypt, signup, webhook

STEPS = {
    "database": database.run,
    "migrate": migrate.run,
    "admin": admin.run,
    "webhook": webhook.run,
    "branding": branding.run,
    "features": features.run,
    "signup": signup.run,
    "reencrypt": reencrypt.run,
}
ORDER = ["migrate", "admin", "webhook", "branding", "features", "signup"]
# Steps that write only when told to. --apply means nothing to the others.
APPLY_STEPS = {"reencrypt"}


def main(argv: list[str]) -> None:
    if not argv or argv[0] not in {"all", *STEPS}:
        raise SystemExit(__doc__)
    remove = "--remove" in argv[1:]
    apply = "--apply" in argv[1:]
    names = ORDER if argv[0] == "all" else [argv[0]]
    for name in reversed(names) if remove else names:
        kwargs = {"apply": apply} if name in APPLY_STEPS else {}
        print(STEPS[name](remove=remove, **kwargs))


if __name__ == "__main__":
    main(sys.argv[1:])
