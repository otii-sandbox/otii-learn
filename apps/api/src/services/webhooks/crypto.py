"""
Cryptographic helpers for webhook secrets.

- Fernet symmetric encryption for storing secrets at rest (key derived from JWT secret;
  otii: previous JWT keys still decrypt, see src/otii/key_ring.py).
- HMAC-SHA256 for signing outgoing payloads.
"""

import base64
import functools
import hashlib
import hmac

from cryptography.fernet import Fernet, MultiFernet

from config.config import get_learnhouse_config
from src.otii.key_ring import previous_keys


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


@functools.lru_cache(maxsize=1)
def _fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from the application's JWT secret."""
    secret = get_learnhouse_config().security_config.auth_jwt_secret_key
    return _derive_fernet_key(secret)


@functools.lru_cache(maxsize=1)
def _previous_fernet_keys() -> tuple[bytes, ...]:
    """otii: Fernet keys of LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS, newest first."""
    current = get_learnhouse_config().security_config.auth_jwt_secret_key
    return tuple(_derive_fernet_key(k) for k in previous_keys(current))


def webhook_fernet_ring() -> list[Fernet]:
    """otii: the keys a stored webhook secret may be under, current first."""
    return [Fernet(k) for k in (_fernet_key(), *_previous_fernet_keys())]


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a webhook signing secret for database storage."""
    f = Fernet(_fernet_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored webhook signing secret.

    otii: tries the current key, then each previous key of the ring, so a key
    rotation does not stop deliveries before the re-encrypt step has run.
    """
    f = MultiFernet(webhook_fernet_ring())
    return f.decrypt(ciphertext.encode()).decode()


def compute_signature(payload: bytes, secret: str) -> str:
    """
    Compute an HMAC-SHA256 signature for a webhook payload.

    Returns a string in the form ``sha256=<hex_digest>``, matching the
    convention used by GitHub, Stripe, and supported by Zapier / Make.com.
    """
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"
