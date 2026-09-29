"""python -m src.otii.setup {all|webhook|branding} [--remove]"""

from __future__ import annotations

import sys

from src.otii.setup import branding, webhook

STEPS = {"webhook": webhook.run, "branding": branding.run}
ORDER = ["webhook", "branding"]


def main(argv: list[str]) -> None:
    if not argv or argv[0] not in {"all", *STEPS}:
        raise SystemExit(__doc__)
    remove = "--remove" in argv[1:]
    names = ORDER if argv[0] == "all" else [argv[0]]
    for name in reversed(names) if remove else names:
        print(STEPS[name](remove=remove))


if __name__ == "__main__":
    main(sys.argv[1:])
