"""JWT key rotation: LEARNHOUSE_AUTH_JWT_SECRET_KEY current, ..._PREVIOUS read only.

Old values are made here with the stored formats written out by hand (not with
the app's helpers), so these tests also prove the formats did not change.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlmodel import select

import src.security.auth as auth_module
import src.security.security as security_module
import src.services.auth.mfa as mfa_module
import src.services.nudges.tokens as nudge_tokens
import src.services.webhooks.crypto as webhook_crypto
from src.otii.key_ring import PREVIOUS_ENV, previous_keys
from src.security.security import ALGORITHM, SECRET_KEY

OLD_KEY = "old-jwt-key-that-was-leaked-0123456789abcdef"
OTHER_KEY = "a-key-this-deployment-never-had-0123456789"
API_DIR = Path(__file__).resolve().parents[3]


# Stored formats, written out independently of the app code.
def webhook_ciphertext(key: str, plaintext: str) -> str:
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    return Fernet(fernet_key).encrypt(plaintext.encode()).decode()


def mfa_ciphertext(material: str, plaintext: str) -> str:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b"learnhouse-mfa-secret-encryption-v1").derive(material.encode())
    return Fernet(base64.urlsafe_b64encode(derived)).encrypt(plaintext.encode()).decode()


def backup_code_hash(key: str, code: str) -> str:
    normalized = "".join(ch for ch in code if ch.isalnum()).upper()
    return hashlib.sha256(key.encode() + normalized.encode()).hexdigest()


def api_token_hash(key: str, token: str) -> str:
    pepper = base64.b64encode(key.encode()).decode("ascii")
    return Argon2Hasher().hash(f"{pepper}:{token}")


def unsubscribe_token(key: str, user_uuid: str, category: str = "lifecycle") -> str:
    secret = hashlib.sha256(b"learnhouse.unsubscribe.v1|" + key.encode()).digest()
    payload = base64.urlsafe_b64encode(user_uuid.encode()).decode().rstrip("=")
    import hmac

    sig = hmac.new(secret, f"{category}:{user_uuid}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def signed(key: str, **claims) -> str:
    now = datetime.now(timezone.utc)
    body = {"sub": "user@test.com", "iat": now, "exp": now + timedelta(hours=1), "type": "access"}
    body.update(claims)
    return jwt.encode(body, key, algorithm=ALGORITHM)


@pytest.fixture
def previous(monkeypatch):
    """Put OLD_KEY in the ring of every module that reads it."""
    monkeypatch.setenv(PREVIOUS_ENV, OLD_KEY)
    monkeypatch.setattr(security_module, "PREVIOUS_SECRET_KEYS", (OLD_KEY,))
    monkeypatch.setattr(
        security_module, "_PREVIOUS_TOKEN_PEPPERS_B64", (security_module._pepper_b64(OLD_KEY),)
    )
    monkeypatch.setattr(auth_module, "JWT_PREVIOUS_SECRET_KEYS", (OLD_KEY,))
    monkeypatch.setattr(mfa_module, "PREVIOUS_SECRET_KEYS", (OLD_KEY,))
    monkeypatch.setattr(mfa_module, "_PREVIOUS_BACKUP_PEPPERS", (OLD_KEY.encode(),))
    yield


@pytest.fixture(autouse=True)
def fresh_key_caches(monkeypatch):
    monkeypatch.delenv("LEARNHOUSE_MFA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(mfa_module, "_fernet_cached", None)
    monkeypatch.setattr(mfa_module, "_fernet_ring_cached", None)
    webhook_crypto._fernet_key.cache_clear()
    webhook_crypto._previous_fernet_keys.cache_clear()
    nudge_tokens._unsub_secret.cache_clear()
    yield
    webhook_crypto._fernet_key.cache_clear()
    webhook_crypto._previous_fernet_keys.cache_clear()
    nudge_tokens._unsub_secret.cache_clear()


class TestPreviousKeysSetting:
    def test_unset_or_empty_means_no_previous_keys(self):
        assert previous_keys(SECRET_KEY, "") == ()
        assert previous_keys(SECRET_KEY, " , ,") == ()

    def test_newest_first_without_blanks_repeats_or_the_current_key(self):
        raw = f" {OLD_KEY} ,,{SECRET_KEY},{OTHER_KEY},{OLD_KEY}"
        assert previous_keys(SECRET_KEY, raw) == (OLD_KEY, OTHER_KEY)

    def test_a_key_too_short_to_have_ever_been_valid_is_refused(self):
        with pytest.raises(ValueError, match="shorter than 32"):
            previous_keys(SECRET_KEY, "short")

    def test_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv(PREVIOUS_ENV, OLD_KEY)
        assert previous_keys(SECRET_KEY) == (OLD_KEY,)
        monkeypatch.delenv(PREVIOUS_ENV)
        assert previous_keys(SECRET_KEY) == ()


class TestLoginTokens:
    def test_new_tokens_are_signed_with_the_current_key(self, previous):
        token = auth_module.create_access_token({"sub": "user@test.com"})
        assert jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["sub"] == "user@test.com"
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, OLD_KEY, algorithms=[ALGORITHM])
        refresh = auth_module.create_refresh_token({"sub": "user@test.com"})
        jwt.decode(refresh, SECRET_KEY, algorithms=[ALGORITHM])

    def test_a_token_signed_with_the_previous_key_still_verifies(self, previous):
        assert auth_module.decode_jwt(signed(OLD_KEY))["sub"] == "user@test.com"
        refresh = signed(OLD_KEY, type="refresh", jti="j1")
        assert auth_module.decode_refresh_token(refresh)["jti"] == "j1"

    def test_without_the_previous_key_an_old_token_is_refused(self):
        assert auth_module.JWT_PREVIOUS_SECRET_KEYS == ()
        assert auth_module.decode_jwt(signed(OLD_KEY)) is None
        assert auth_module.decode_refresh_token(signed(OLD_KEY, type="refresh", jti="j1")) is None

    def test_a_key_outside_the_ring_is_refused(self, previous):
        assert auth_module.decode_jwt(signed(OTHER_KEY)) is None

    def test_the_previous_key_does_not_widen_other_checks(self, previous):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        assert auth_module.decode_jwt(signed(OLD_KEY, exp=past)) is None, "expired stays expired"
        assert auth_module.decode_refresh_token(signed(OLD_KEY)) is None, "access token is not a refresh token"
        no_sub = jwt.encode({"exp": datetime.now(timezone.utc) + timedelta(hours=1)}, OLD_KEY, algorithm=ALGORITHM)
        assert auth_module.decode_jwt(no_sub) is None, "required claims still required"
        assert auth_module.decode_jwt("not-a-jwt") is None
        assert auth_module.decode_jwt(jwt.encode({"sub": "x"}, OLD_KEY, algorithm="HS512")) is None

    def test_current_key_tokens_still_verify_during_a_rotation(self, previous):
        assert auth_module.decode_jwt(signed(SECRET_KEY))["sub"] == "user@test.com"


class TestWebhookSecrets:
    def test_new_values_use_the_current_key(self, previous):
        stored = webhook_crypto.encrypt_secret("whsec")
        current = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest()))
        assert current.decrypt(stored.encode()).decode() == "whsec"

    def test_a_value_under_the_previous_key_decrypts(self, previous):
        assert webhook_crypto.decrypt_secret(webhook_ciphertext(OLD_KEY, "whsec")) == "whsec"

    def test_a_value_already_stored_under_the_current_key_decrypts(self, previous):
        assert webhook_crypto.decrypt_secret(webhook_ciphertext(SECRET_KEY, "whsec")) == "whsec"

    def test_without_the_previous_key_it_does_not(self):
        with pytest.raises(Exception):
            webhook_crypto.decrypt_secret(webhook_ciphertext(OLD_KEY, "whsec"))

    def test_a_key_outside_the_ring_does_not(self, previous):
        with pytest.raises(Exception):
            webhook_crypto.decrypt_secret(webhook_ciphertext(OTHER_KEY, "whsec"))


class TestMFASecrets:
    def test_new_values_use_the_current_key(self, previous):
        stored = mfa_module.encrypt_secret("JBSWY3DPEHPK3PXP")
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                       info=b"learnhouse-mfa-secret-encryption-v1").derive(SECRET_KEY.encode())
        assert Fernet(base64.urlsafe_b64encode(derived)).decrypt(stored.encode()) == b"JBSWY3DPEHPK3PXP"

    def test_a_value_under_the_previous_key_decrypts(self, previous):
        assert mfa_module.decrypt_secret(mfa_ciphertext(OLD_KEY, "JBSWY3DPEHPK3PXP")) == "JBSWY3DPEHPK3PXP"

    def test_without_the_previous_key_it_reads_as_broken(self):
        assert mfa_module.decrypt_secret(mfa_ciphertext(OLD_KEY, "JBSWY3DPEHPK3PXP")) is None

    def test_a_key_outside_the_ring_reads_as_broken(self, previous):
        assert mfa_module.decrypt_secret(mfa_ciphertext(OTHER_KEY, "JBSWY3DPEHPK3PXP")) is None

    def test_a_dedicated_mfa_key_is_used_alone_as_before(self, previous, monkeypatch):
        dedicated = "dedicated-mfa-encryption-key-0123456789abcd"
        monkeypatch.setenv("LEARNHOUSE_MFA_ENCRYPTION_KEY", dedicated)
        assert len(mfa_module.mfa_fernet_ring()) == 1
        assert mfa_module.decrypt_secret(mfa_ciphertext(dedicated, "S")) == "S"
        assert mfa_module.decrypt_secret(mfa_ciphertext(OLD_KEY, "S")) is None
        stored = mfa_module.encrypt_secret("S2")
        assert mfa_module.decrypt_secret(stored) == "S2"
        derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                       info=b"learnhouse-mfa-secret-encryption-v1").derive(dedicated.encode())
        assert Fernet(base64.urlsafe_b64encode(derived)).decrypt(stored.encode()) == b"S2"

    def test_backup_codes_hash_under_the_current_key(self, previous):
        assert mfa_module.hash_backup_code("abcde-fghjk") == backup_code_hash(SECRET_KEY, "ABCDEFGHJK")
        assert mfa_module.backup_code_hashes("ABCDE-FGHJK") == [
            backup_code_hash(SECRET_KEY, "ABCDEFGHJK"), backup_code_hash(OLD_KEY, "ABCDEFGHJK")]

    async def test_a_backup_code_issued_under_the_previous_key_is_consumed_once(self, previous, db, regular_user):
        from src.db.user_mfa import UserMFABackupCode

        db.add(UserMFABackupCode(user_id=regular_user.id, code_hash=backup_code_hash(OLD_KEY, "ABCDEFGHJK"),
                                 creation_date=str(datetime.now())))
        await db.commit()
        assert await mfa_module.consume_backup_code(db, regular_user.id, "abcde-fghjk") is True
        assert await mfa_module.consume_backup_code(db, regular_user.id, "abcde-fghjk") is False

    async def test_without_the_previous_key_that_code_is_refused(self, db, regular_user):
        from src.db.user_mfa import UserMFABackupCode

        db.add(UserMFABackupCode(user_id=regular_user.id, code_hash=backup_code_hash(OLD_KEY, "ABCDEFGHJK"),
                                 creation_date=str(datetime.now())))
        await db.commit()
        assert await mfa_module.consume_backup_code(db, regular_user.id, "ABCDE-FGHJK") is False


class TestAPITokens:
    def test_a_hash_under_the_current_key_is_valid_and_kept(self, previous):
        assert security_module.security_check_token("lh_tok", api_token_hash(SECRET_KEY, "lh_tok")) == (True, False)

    def test_a_hash_under_the_previous_key_is_valid_and_re_hashed(self, previous):
        assert security_module.security_check_token("lh_tok", api_token_hash(OLD_KEY, "lh_tok")) == (True, True)
        assert security_module.security_verify_token("lh_tok", api_token_hash(OLD_KEY, "lh_tok")) is True

    def test_without_the_previous_key_it_is_refused(self):
        assert security_module.security_check_token("lh_tok", api_token_hash(OLD_KEY, "lh_tok")) == (False, False)

    def test_a_wrong_token_or_foreign_key_is_refused(self, previous):
        assert security_module.security_check_token("lh_bad", api_token_hash(OLD_KEY, "lh_tok")) == (False, False)
        assert security_module.security_check_token("lh_tok", api_token_hash(OTHER_KEY, "lh_tok")) == (False, False)

    def test_legacy_sha256_hashes_still_verify_and_re_hash(self, previous):
        legacy = hashlib.sha256(b"lh_tok").hexdigest()
        assert security_module.security_check_token("lh_tok", legacy) == (True, True)
        assert security_module.security_check_token("lh_bad", legacy) == (False, False)

    async def test_org_token_moves_to_the_current_key_at_its_next_use(self, previous, db, org):
        from src.db.api_tokens import APIToken
        from src.services.api_tokens.api_tokens import validate_api_token_for_auth

        token = "lh_rotation_tok"
        db.add(APIToken(token_uuid="apitoken_rot", name="t", token_prefix=token[:12],
                        token_hash=api_token_hash(OLD_KEY, token), org_id=org.id, created_by_user_id=1))
        await db.commit()
        found = await validate_api_token_for_auth(token, db)
        assert found is not None
        row = (await db.execute(select(APIToken).where(APIToken.token_uuid == "apitoken_rot"))).scalars().one()
        assert security_module.security_check_token(token, row.token_hash) == (True, False)

    async def test_superadmin_token_moves_to_the_current_key_at_its_next_use(self, previous, db, admin_user):
        from src.db.superadmin_api_tokens import SuperadminAPIToken
        from src.services.api_tokens.superadmin_api_tokens import validate_superadmin_token_for_auth

        token = "lh_sa_rotation_tok"
        db.add(SuperadminAPIToken(token_uuid="satoken_rot", name="t", token_prefix=token[:15],
                                  token_hash=api_token_hash(OLD_KEY, token), created_by_user_id=admin_user.id))
        await db.commit()
        assert await validate_superadmin_token_for_auth(token, db) is not None
        row = (await db.execute(select(SuperadminAPIToken).where(
            SuperadminAPIToken.token_uuid == "satoken_rot"))).scalars().one()
        assert security_module.security_check_token(token, row.token_hash) == (True, False)

    async def test_a_current_key_token_is_not_re_hashed(self, previous, db, org):
        from src.db.api_tokens import APIToken
        from src.services.api_tokens.api_tokens import validate_api_token_for_auth

        token = "lh_current_tk"
        stored = api_token_hash(SECRET_KEY, token)
        db.add(APIToken(token_uuid="apitoken_cur", name="t", token_prefix=token[:12],
                        token_hash=stored, org_id=org.id, created_by_user_id=1))
        await db.commit()
        assert await validate_api_token_for_auth(token, db) is not None
        row = (await db.execute(select(APIToken).where(APIToken.token_uuid == "apitoken_cur"))).scalars().one()
        assert row.token_hash == stored


class TestUnsubscribeLinks:
    def test_new_links_use_the_current_key(self, previous):
        assert nudge_tokens.make_unsubscribe_token("user_1") == unsubscribe_token(SECRET_KEY, "user_1")

    def test_a_link_signed_with_the_previous_key_still_verifies(self, previous):
        assert nudge_tokens.verify_unsubscribe_token(unsubscribe_token(OLD_KEY, "user_1")) == "user_1"

    def test_without_the_previous_key_it_does_not(self):
        assert nudge_tokens.verify_unsubscribe_token(unsubscribe_token(OLD_KEY, "user_1")) is None

    def test_a_previous_key_link_for_another_category_is_refused(self, previous):
        assert nudge_tokens.verify_unsubscribe_token(unsubscribe_token(OLD_KEY, "user_1", "other")) is None


def test_the_setting_reaches_every_reader_through_the_environment():
    """No monkeypatching: a fresh interpreter with only the two settings set."""
    script = r"""
import base64, hashlib, json, sys
sys.path.insert(0, ".")
old = sys.argv[1]
values = json.loads(sys.argv[2])
from src.security import auth, security
from src.services.auth import mfa
from src.services.webhooks import crypto
from src.services.nudges import tokens
def webhook(value):
    try:
        return crypto.decrypt_secret(value)
    except Exception:
        return None
print(json.dumps({
    "jwt": (auth.decode_jwt(values["jwt"]) or {}).get("sub"),
    "webhook": webhook(values["webhook"]),
    "mfa": mfa.decrypt_secret(values["mfa"]),
    "backup": values["backup"] in mfa.backup_code_hashes("ABCDE-FGHJK"),
    "api": security.security_check_token("lh_tok", values["api"]),
    "unsub": tokens.verify_unsubscribe_token(values["unsub"]),
    "new_is_current": security.SECRET_KEY != old,
}))
"""
    values = {
        "jwt": signed(OLD_KEY),
        "webhook": webhook_ciphertext(OLD_KEY, "whsec"),
        "mfa": mfa_ciphertext(OLD_KEY, "JBSWY3DPEHPK3PXP"),
        "backup": backup_code_hash(OLD_KEY, "ABCDEFGHJK"),
        "api": api_token_hash(OLD_KEY, "lh_tok"),
        "unsub": unsubscribe_token(OLD_KEY, "user_1"),
    }
    env = {k: v for k, v in os.environ.items() if k != "LEARNHOUSE_MFA_ENCRYPTION_KEY"}
    env.update({"TESTING": "true", "LEARNHOUSE_AUTH_JWT_SECRET_KEY": "the-new-key-after-rotation-0123456789abcdef",
                PREVIOUS_ENV: OLD_KEY})
    out = subprocess.run([sys.executable, "-c", script, OLD_KEY, json.dumps(values)],
                         cwd=API_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got == {"jwt": "user@test.com", "webhook": "whsec", "mfa": "JBSWY3DPEHPK3PXP", "backup": True,
                   "api": [True, True], "unsub": "user_1", "new_is_current": True}

    env.pop(PREVIOUS_ENV)
    out = subprocess.run([sys.executable, "-c", script, OLD_KEY, json.dumps(values)],
                         cwd=API_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["jwt"] is None and got["webhook"] is None and got["mfa"] is None and got["backup"] is False
    assert got["api"] == [False, False] and got["unsub"] is None


def test_a_mistyped_previous_key_stops_the_app_at_start():
    env = dict(os.environ, TESTING="true", **{PREVIOUS_ENV: "too-short"})
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import src.security.security"],
                         cwd=API_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode != 0
    assert "shorter than 32" in out.stderr
    assert "too-short" not in out.stderr, "the value itself is never printed"
