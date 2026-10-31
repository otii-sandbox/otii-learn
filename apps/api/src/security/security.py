from typing import Tuple
import hashlib
import base64
import secrets
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from config.config import get_learnhouse_config
from src.otii.key_ring import previous_keys


### 🔒 JWT ##############################################################

ACCESS_TOKEN_EXPIRE_MINUTES = 30
SECRET_KEY = get_learnhouse_config().security_config.auth_jwt_secret_key
# otii: the key ring. SECRET_KEY signs, encrypts and hashes; the previous keys
# (LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS) are only ever used to read. See
# src/otii/key_ring.py.
PREVIOUS_SECRET_KEYS = previous_keys(SECRET_KEY)
ALGORITHM = "HS256"

### 🔒 JWT ##############################################################


### 🔒 Passwords Hashing ##############################################################


class Pbkdf2Sha256Hasher:
    """
    Custom hasher for legacy passlib pbkdf2_sha256 hashes.

    Passlib format: $pbkdf2-sha256$rounds$salt$checksum
    - salt and checksum are base64 encoded (using passlib's ab64 variant)

    This hasher only supports verification (for migration), not creating new hashes.
    """

    @classmethod
    def identify(cls, hash: str | bytes) -> bool:
        """Check if this is a passlib pbkdf2-sha256 hash."""
        if isinstance(hash, bytes):
            hash = hash.decode('utf-8')
        return hash.startswith('$pbkdf2-sha256$')

    def hash(self, password: str | bytes, salt: bytes | None = None) -> str:
        """
        Not implemented - we don't create new pbkdf2 hashes.
        All new hashes should use Argon2.
        """
        raise NotImplementedError("Pbkdf2Sha256Hasher is for verification only. Use Argon2 for new hashes.")

    def verify(self, password: str | bytes, hash: str | bytes) -> bool:
        """Verify a password against a passlib pbkdf2-sha256 hash."""
        if isinstance(hash, bytes):
            hash = hash.decode('utf-8')
        if isinstance(password, str):
            password = password.encode('utf-8')

        try:
            # Parse passlib format: $pbkdf2-sha256$rounds$salt$checksum
            parts = hash.split('$')
            if len(parts) != 5 or parts[1] != 'pbkdf2-sha256':
                return False

            rounds = int(parts[2])
            # Passlib uses a variant of base64 (ab64) - replace . with +
            salt_b64 = parts[3].replace('.', '+')
            checksum_b64 = parts[4].replace('.', '+')

            # Add padding if needed
            salt_b64 += '=' * (-len(salt_b64) % 4)
            checksum_b64 += '=' * (-len(checksum_b64) % 4)

            salt = base64.b64decode(salt_b64)
            expected_checksum = base64.b64decode(checksum_b64)

            # Compute the hash
            computed = hashlib.pbkdf2_hmac(
                'sha256',
                password,
                salt,
                rounds,
                dklen=len(expected_checksum)
            )

            # Constant-time comparison
            return secrets.compare_digest(computed, expected_checksum)

        except (ValueError, IndexError, base64.binascii.Error):
            return False

    def check_needs_rehash(self, hash: str | bytes) -> bool:
        """Always return True - we want to migrate away from pbkdf2."""
        return True


# Password hasher with migration support
# Primary: Argon2 (all new hashes)
# Legacy: pbkdf2_sha256 (verification only, will be migrated)
password_hash = PasswordHash((
    Argon2Hasher(),
    Pbkdf2Sha256Hasher(),
))


def security_hash_password(password: str) -> str:
    """
    Hash a password using Argon2.
    """
    return password_hash.hash(password)


def security_verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash using constant-time comparison.

    Supports multiple hash formats for backwards compatibility:
    - Argon2 (current)
    - pbkdf2_sha256 (legacy passlib format)
    """
    return password_hash.verify(plain_password, hashed_password)


def security_verify_and_update_password(plain_password: str, hashed_password: str) -> Tuple[bool, str | None]:
    """
    Verify a password and return a new hash if the current one uses a deprecated algorithm.

    This enables transparent migration from old hash formats (pbkdf2_sha256)
    to Argon2 without requiring users to reset their passwords.

    Args:
        plain_password: The plaintext password to verify
        hashed_password: The stored password hash

    Returns:
        Tuple of (is_valid, new_hash)
        - is_valid: True if password matches
        - new_hash: New Argon2 hash if migration needed, None otherwise
    """
    return password_hash.verify_and_update(plain_password, hashed_password)


### 🔒 Passwords Hashing ##############################################################


### 🔒 API token hashing ##############################################################


def _pepper_b64(key) -> str:
    raw = key.encode() if isinstance(key, str) else bytes(key)
    return base64.b64encode(raw).decode("ascii")


_TOKEN_PEPPER = SECRET_KEY.encode() if isinstance(SECRET_KEY, str) else bytes(SECRET_KEY)
_TOKEN_PEPPER_B64 = _pepper_b64(SECRET_KEY)
# otii: hashes made under a previous key still match until the token is next
# used, when it is re-hashed under the current key (see security_check_token).
_PREVIOUS_TOKEN_PEPPERS_B64 = tuple(_pepper_b64(k) for k in PREVIOUS_SECRET_KEYS)
_token_hasher = Argon2Hasher()


def _token_input(token: str, pepper_b64: str | None = None) -> str:
    return f"{pepper_b64 or _TOKEN_PEPPER_B64}:{token}"


def _is_legacy_sha256_hash(stored_hash: str) -> bool:
    # Legacy hashes were pepperless SHA-256 hex digests — exactly 64 lowercase hex chars.
    if not isinstance(stored_hash, str) or len(stored_hash) != 64:
        return False
    try:
        int(stored_hash, 16)
    except ValueError:
        return False
    return stored_hash == stored_hash.lower()


def security_hash_token(token: str) -> str:
    return _token_hasher.hash(_token_input(token))


def _argon2_token_matches(provided_token: str, stored_hash: str, pepper_b64: str) -> bool:
    try:
        return _token_hasher.verify(_token_input(provided_token, pepper_b64), stored_hash)
    except Exception:
        return False


def security_check_token(provided_token: str, stored_hash: str) -> Tuple[bool, bool]:
    """Return ``(valid, needs_rehash)`` for a presented token.

    ``needs_rehash`` is True when the stored hash is the legacy pepperless
    SHA-256 shape, or (otii) when it only matched under a previous key of the
    ring: the caller then stores ``security_hash_token(token)``.
    """
    # Backwards compatibility: tokens created before the argon2id switch were
    # stored as pepperless SHA-256 hex. Detect that shape and verify directly
    # so existing customer tokens keep authenticating.
    if _is_legacy_sha256_hash(stored_hash):
        provided_hash = hashlib.sha256(provided_token.encode()).hexdigest()
        valid = secrets.compare_digest(provided_hash, stored_hash)
        return valid, valid
    if _argon2_token_matches(provided_token, stored_hash, _TOKEN_PEPPER_B64):
        return True, False
    for pepper_b64 in _PREVIOUS_TOKEN_PEPPERS_B64:
        if _argon2_token_matches(provided_token, stored_hash, pepper_b64):
            return True, True
    return False, False


def security_verify_token(provided_token: str, stored_hash: str) -> bool:
    return security_check_token(provided_token, stored_hash)[0]


def security_token_needs_rehash(stored_hash: str) -> bool:
    """True when ``stored_hash`` uses a deprecated scheme that should be upgraded."""
    return _is_legacy_sha256_hash(stored_hash)


### 🔒 API token hashing ##############################################################
