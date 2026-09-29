"""python -m src.otii.setup {all|database|migrate|webhook|branding|features} [--remove]

`all` runs the steps that need a running Otii Learn: migrate, webhook,
branding, features. `database` runs before the API exists, on its own.
"""

from __future__ import annotations

import sys

from src.otii.setup import branding, database, features, migrate, webhook

STEPS = {
    "database": database.run,
    "migrate": migrate.run,
    "webhook": webhook.run,
    "branding": branding.run,
    "features": features.run,
}
ORDER = ["migrate", "webhook", "branding", "features"]


def main(argv: list[str]) -> None:
    if not argv or argv[0] not in {"all", *STEPS}:
        raise SystemExit(__doc__)
    remove = "--remove" in argv[1:]
    names = ORDER if argv[0] == "all" else [argv[0]]
    for name in reversed(names) if remove else names:
        print(STEPS[name](remove=remove))


if __name__ == "__main__":
    main(sys.argv[1:])
