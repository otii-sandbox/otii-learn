"""The JWT key ring: rotate LEARNHOUSE_AUTH_JWT_SECRET_KEY without losing data.

LEARNHOUSE_AUTH_JWT_SECRET_KEY signs login tokens and is also the root of
everything LearnHouse keeps under a key: webhook signing secrets and 2FA
secrets (encrypted), API-token and 2FA backup-code hashes (peppered), and
unsubscribe links (signed). A rotation sets:

    LEARNHOUSE_AUTH_JWT_SECRET_KEY           the new key: signs, encrypts, hashes
    LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS  the old key(s), comma-separated,
                                             newest first: read only

Previous keys are only ever used to read (verify a token, decrypt a value,
match a hash). Every new value uses the current key. Outside a rotation the
previous setting is empty or unset.

Rotation order:
1. new key in LEARNHOUSE_AUTH_JWT_SECRET_KEY, old key in ..._PREVIOUS; restart.
2. ``python -m src.otii.setup reencrypt`` (dry run, counts), then with
   ``--apply``: every encrypted value moves to the new key.
3. Dry run again shows nothing left on a previous key; empty ..._PREVIOUS; restart.

While a previous key is set, a login token signed with it is still accepted.
After a leak that means whoever holds the leaked key can mint tokens until
..._PREVIOUS is emptied, so keep that window short.
"""

from __future__ import annotations

import os

PREVIOUS_ENV = "LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS"
MIN_KEY_LENGTH = 32  # same rule config.py applies to the current key


def previous_keys(current: str, raw: str | None = None) -> tuple[str, ...]:
    """The previous keys, newest first, without blanks, repeats or the current key.

    Refuses (ValueError, at start-up) a previous key shorter than any key the
    app would ever have accepted as current: it can only be a typo.
    """
    if raw is None:
        raw = os.environ.get(PREVIOUS_ENV, "")
    keys: list[str] = []
    for part in raw.split(","):
        key = part.strip()
        if not key or key == current or key in keys:
            continue
        if len(key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"SECURITY ERROR: an entry of {PREVIOUS_ENV} is shorter than "
                f"{MIN_KEY_LENGTH} characters, so it was never a valid key. "
                "Give the old keys exactly as they were, comma-separated, newest first."
            )
        keys.append(key)
    return tuple(keys)
