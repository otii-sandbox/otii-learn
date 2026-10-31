"""The setup step that moves stored values onto the current JWT key."""

from datetime import datetime

import pytest
from sqlalchemy import select, update

import src.security.security as security_module
import src.services.auth.mfa as mfa_module
import src.services.webhooks.crypto as webhook_crypto
from src.db.api_tokens import APIToken
from src.db.user_mfa import UserMFA, UserMFABackupCode
from src.db.webhooks import WebhookEndpoint
from src.otii.key_ring import PREVIOUS_ENV
from src.otii.setup import __main__ as steps
from src.otii.setup import reencrypt as step
from src.security.security import SECRET_KEY
from src.tests.otii.test_key_rotation import OLD_KEY, OTHER_KEY, mfa_ciphertext, webhook_ciphertext


@pytest.fixture(autouse=True)
def ring(monkeypatch):
    monkeypatch.delenv("LEARNHOUSE_MFA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv(PREVIOUS_ENV, OLD_KEY)
    monkeypatch.setattr(security_module, "PREVIOUS_SECRET_KEYS", (OLD_KEY,))
    monkeypatch.setattr(mfa_module, "PREVIOUS_SECRET_KEYS", (OLD_KEY,))
    monkeypatch.setattr(mfa_module, "_fernet_cached", None)
    monkeypatch.setattr(mfa_module, "_fernet_ring_cached", None)
    webhook_crypto._fernet_key.cache_clear()
    webhook_crypto._previous_fernet_keys.cache_clear()
    yield
    webhook_crypto._fernet_key.cache_clear()
    webhook_crypto._previous_fernet_keys.cache_clear()


def current_only(value: str, kind: str) -> str:
    """Open a value with the current key alone: proves it was moved."""
    from cryptography.fernet import Fernet

    if kind == "webhook":
        return Fernet(webhook_crypto._fernet_key()).decrypt(value.encode()).decode()
    return mfa_module._get_fernet().decrypt(value.encode()).decode()


@pytest.fixture
async def stored(db, org, admin_user, regular_user):
    """One value of each kind on each key, plus an empty one and one no key opens."""
    now = str(datetime.now())

    def hook(uuid, secret):
        return WebhookEndpoint(webhook_uuid=uuid, org_id=org.id, url="https://example.test/h",
                               secret_encrypted=secret, events=["course_completed"],
                               created_by_user_id=admin_user.id, creation_date=now, update_date=now)

    db.add_all([
        hook("w_old", webhook_ciphertext(OLD_KEY, "secret-old")),
        hook("w_cur", webhook_ciphertext(SECRET_KEY, "secret-cur")),
        hook("w_empty", ""),
        hook("w_lost", webhook_ciphertext(OTHER_KEY, "secret-lost")),
        UserMFA(user_id=admin_user.id, secret_encrypted=mfa_ciphertext(OLD_KEY, "TOTPOLD"), confirmed_at=now),
        UserMFA(user_id=regular_user.id, secret_encrypted=mfa_ciphertext(SECRET_KEY, "TOTPCUR"), confirmed_at=now),
        UserMFABackupCode(user_id=admin_user.id, code_hash="a" * 64, creation_date=now),
        UserMFABackupCode(user_id=admin_user.id, code_hash="b" * 64, creation_date=now, used_at=now),
        APIToken(token_uuid="apitoken_1", name="t", token_prefix="lh_abc", token_hash="x",
                 org_id=org.id, created_by_user_id=admin_user.id),
    ])
    await db.commit()
    return db


async def snapshot(engine):
    async with engine.connect() as conn:
        hooks = dict((await conn.execute(select(WebhookEndpoint.webhook_uuid, WebhookEndpoint.secret_encrypted))).all())
        mfa = dict((await conn.execute(select(UserMFA.user_id, UserMFA.secret_encrypted))).all())
    return hooks, mfa


async def test_dry_run_counts_and_writes_nothing(engine, stored, admin_user):
    before = await snapshot(engine)
    report, unreadable = await step.reencrypt(engine, apply=False)
    assert await snapshot(engine) == before
    assert unreadable == 1
    assert "dry run, nothing written" in report
    assert ("webhook_endpoint.secret_encrypted: 1 on the current key, 1 on a previous key (would move), "
            "1 open under no key, 1 empty") in report
    assert "usermfa.secret_encrypted: 1 on the current key, 1 on a previous key (would move), 0 open under no key" in report
    assert "usermfabackupcode.code_hash: 1 unused backup codes of 1 people" in report
    assert "apitoken.token_hash: 1 active" in report
    assert "superadmin_apitoken.token_hash: 0 active" in report
    assert "previous keys in the ring: 1" in report


async def test_apply_moves_each_value_to_the_current_key_and_changes_nothing_else(
    engine, stored, admin_user, regular_user, monkeypatch
):
    hooks_before, mfa_before = await snapshot(engine)
    report, unreadable = await step.reencrypt(engine, apply=True)
    hooks, mfa = await snapshot(engine)

    assert current_only(hooks["w_old"], "webhook") == "secret-old"
    assert current_only(mfa[admin_user.id], "mfa") == "TOTPOLD"
    assert hooks["w_cur"] == hooks_before["w_cur"], "a value already on the current key is not rewritten"
    assert mfa[regular_user.id] == mfa_before[regular_user.id]
    assert hooks["w_empty"] == "" and hooks["w_lost"] == hooks_before["w_lost"]
    assert "webhook_endpoint.secret_encrypted: 1 on the current key, 1 moved to the current key, 0 changed" in report
    assert "usermfa.secret_encrypted: 1 on the current key, 1 moved to the current key, 0 changed" in report
    assert unreadable == 1

    # The app reads the moved values with the previous key gone.
    monkeypatch.setattr(mfa_module, "PREVIOUS_SECRET_KEYS", ())
    monkeypatch.setattr(mfa_module, "_fernet_ring_cached", None)
    assert mfa_module.decrypt_secret(mfa[admin_user.id]) == "TOTPOLD"


async def test_rerun_is_idempotent(engine, stored):
    await step.reencrypt(engine, apply=True)
    after_first = await snapshot(engine)
    report, _ = await step.reencrypt(engine, apply=True)
    assert await snapshot(engine) == after_first
    assert "webhook_endpoint.secret_encrypted: 2 on the current key, 0 moved" in report
    assert "usermfa.secret_encrypted: 2 on the current key, 0 moved" in report


async def test_a_value_the_app_changed_after_the_read_is_left_alone(engine, stored, admin_user):
    cols = step.columns()
    found = await step.scan(engine, cols)
    # The user re-enrols between the read and the write.
    fresh = mfa_module.encrypt_secret("TOTPNEW")
    async with engine.begin() as conn:
        await conn.execute(update(UserMFA.__table__).where(UserMFA.__table__.c.user_id == admin_user.id)
                           .values(secret_encrypted=fresh))
    await step.write(engine, cols, found)
    _, mfa = await snapshot(engine)
    assert mfa[admin_user.id] == fresh
    counts = found["usermfa.secret_encrypted"]
    assert (counts.moved, counts.changed_meanwhile) == (0, 1)
    assert found["webhook_endpoint.secret_encrypted"].moved == 1


async def test_values_are_never_in_the_report(engine, stored):
    hooks, mfa = await snapshot(engine)
    for apply in (False, True):
        report, _ = await step.reencrypt(engine, apply=apply)
        for secret in ["secret-old", "secret-cur", "secret-lost", "TOTPOLD", "TOTPCUR", OLD_KEY, SECRET_KEY,
                       *hooks.values(), *mfa.values()]:
            if secret:
                assert secret not in report


async def test_nothing_unreadable_means_a_clean_report(engine, db, org, admin_user):
    db.add(UserMFA(user_id=admin_user.id, secret_encrypted=mfa_ciphertext(OLD_KEY, "T")))
    await db.commit()
    report, unreadable = await step.reencrypt(engine, apply=True)
    assert unreadable == 0
    assert "open under no key." not in report


def test_run_exits_non_zero_when_a_value_opens_under_no_key(monkeypatch, capsys):
    async def fake(apply):
        return "reencrypt: report", 2

    monkeypatch.setattr(step, "_run", fake)
    with pytest.raises(SystemExit) as exit_:
        step.run(apply=True)
    assert exit_.value.code == 1
    assert "reencrypt: report" in capsys.readouterr().out


def test_run_returns_the_report_when_everything_opens(monkeypatch):
    seen = {}

    async def fake(apply):
        seen["apply"] = apply
        return "reencrypt: report", 0

    monkeypatch.setattr(step, "_run", fake)
    assert step.run() == "reencrypt: report"
    assert seen["apply"] is False, "dry run unless --apply"


def test_remove_has_nothing_to_undo():
    assert "no undo needed" in step.run(remove=True)


def test_the_step_is_its_own_command_not_part_of_all(monkeypatch, capsys):
    assert "reencrypt" in steps.STEPS and "reencrypt" not in steps.ORDER
    calls = []
    monkeypatch.setitem(steps.STEPS, "reencrypt", lambda remove, apply: calls.append((remove, apply)) or "r")
    steps.main(["reencrypt"])
    steps.main(["reencrypt", "--apply"])
    assert calls == [(False, False), (False, True)]
    monkeypatch.setitem(steps.STEPS, "signup", lambda remove: "s")
    steps.main(["signup", "--apply"])  # --apply means nothing to the other steps
    assert capsys.readouterr().out.split() == ["r", "r", "s"]


def test_settings_missing_stop_the_step(monkeypatch):
    monkeypatch.delenv("LEARNHOUSE_SQL_CONNECTION_STRING", raising=False)
    with pytest.raises(SystemExit, match="missing setting LEARNHOUSE_SQL_CONNECTION_STRING"):
        step.run()


async def test_the_command_end_to_end_on_a_database_file(tmp_path):
    """`python -m src.otii.setup reencrypt [--apply]` in a fresh interpreter, settings from the environment."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel

    for table in SQLModel.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
    url = f"sqlite+aiosqlite:///{tmp_path / 'learn.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(UserMFA.__table__.insert().values(user_id=1, secret_encrypted=mfa_ciphertext(OLD_KEY, "T1")))
        await conn.execute(UserMFA.__table__.insert().values(user_id=2, secret_encrypted=mfa_ciphertext(OTHER_KEY, "T2")))
    api_dir = Path(__file__).resolve().parents[3]
    env = {k: v for k, v in os.environ.items() if k != "LEARNHOUSE_MFA_ENCRYPTION_KEY"}
    env.update({"LEARNHOUSE_SQL_CONNECTION_STRING": url, PREVIOUS_ENV: OLD_KEY,
                "LEARNHOUSE_AUTH_JWT_SECRET_KEY": SECRET_KEY})

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "src.otii.setup", "reencrypt", *args], cwd=api_dir,
                              env=env, capture_output=True, text=True, timeout=120)

    async def values():
        async with engine.connect() as conn:
            return dict((await conn.execute(select(UserMFA.user_id, UserMFA.secret_encrypted))).all())

    before = await values()
    dry = cli()
    assert dry.returncode == 1, "one value opens under no key"
    assert "1 on a previous key (would move), 1 open under no key" in dry.stdout
    assert await values() == before

    applied = cli("--apply")
    assert applied.returncode == 1
    assert "1 moved to the current key" in applied.stdout
    after = await values()
    assert current_only(after[1], "mfa") == "T1" and after[2] == before[2]

    async with engine.begin() as conn:
        await conn.execute(UserMFA.__table__.delete().where(UserMFA.__table__.c.user_id == 2))
    clean = cli()
    assert clean.returncode == 0, clean.stderr[-2000:]
    assert "1 on the current key, 0 on a previous key" in clean.stdout
    for out in (dry, applied, clean):
        assert OLD_KEY not in out.stdout + out.stderr and SECRET_KEY not in out.stdout + out.stderr
        assert "T1" not in out.stdout
    await engine.dispose()
