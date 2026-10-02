"""The setup step that closes Otii Learn's public sign-up page."""

from src.otii.setup import __main__ as steps
from src.otii.setup.signup import INVITE_ONLY, OPEN, set_signup_mode


def test_a_current_style_config_is_set_to_invite_only_and_back():
    config = {"config_version": "2.0", "admin_toggles": {"members": {"signup_mode": "open"}, "boards": {"disabled": True}}}
    set_signup_mode(config, INVITE_ONLY)
    assert config["admin_toggles"]["members"]["signup_mode"] == "inviteOnly"
    assert config["admin_toggles"]["boards"] == {"disabled": True}, "nothing else is touched"
    set_signup_mode(config, OPEN)
    assert config["admin_toggles"]["members"]["signup_mode"] == "open"


def test_a_current_style_config_without_a_members_entry_gets_one():
    config = {"config_version": "2.0"}
    set_signup_mode(config, INVITE_ONLY)
    assert config["admin_toggles"]["members"] == {"signup_mode": "inviteOnly"}


def test_an_old_style_config_is_set_under_features():
    config = {"features": {"members": {"enabled": True, "signup_mode": "open", "admin_limit": 1, "limit": 10}}}
    set_signup_mode(config, INVITE_ONLY)
    assert config["features"]["members"] == {"enabled": True, "signup_mode": "inviteOnly", "admin_limit": 1, "limit": 10}


def test_the_step_runs_with_all_and_last():
    assert steps.ORDER[-1] == "signup"
    assert steps.STEPS["signup"].__module__ == "src.otii.setup.signup"
