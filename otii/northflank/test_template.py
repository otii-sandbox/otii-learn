"""Checks on the Northflank template. Standard library only.

    python3 -m pytest otii/northflank/test_template.py     (or: python3 otii/northflank/test_template.py)

Settings groups that hold secrets are created once and never rewritten by a
template run ("create"), and are runtime only ("environment": never handed to
a build). See SECRET_GROUPS in make_template.py.
"""

from __future__ import annotations

import copy
import io
import json
import re
import runpy
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import deploy  # noqa: E402

SECRET_GROUPS = {"learn-secrets", "learn-storage", "otii-learn-shared"}
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|PRIVATE|_KEY$)")
RUN = {"environment": "staging", "s3KeyPrefix": "otii-learn/staging/"}


def template() -> dict:
    return json.loads((HERE / "template.json").read_text())


def groups(t: dict) -> dict[str, dict]:
    return {n["spec"]["name"]: n for n in deploy.nodes(t["spec"]) if n["kind"] == "SecretGroup"}


def guard_problems(t: dict) -> str:
    err = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            deploy.guard(t, RUN)
    except SystemExit:
        return err.getvalue()
    return ""


def test_template_json_is_what_make_template_writes():
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(HERE / "make_template.py", tmp)
        with redirect_stdout(io.StringIO()):
            runpy.run_path(str(Path(tmp) / "make_template.py"), run_name="__main__")
        assert (Path(tmp) / "template.json").read_text() == (HERE / "template.json").read_text(), \
            "template.json is stale: run python3 otii/northflank/make_template.py and commit both"


def test_every_settings_group_is_accounted_for():
    assert set(groups(template())) == {"learn-config", *SECRET_GROUPS, "learn-dbops"}


def test_groups_holding_secrets_are_created_once_and_runtime_only():
    for name in SECRET_GROUPS:
        node = groups(template())[name]
        assert node["updateMode"] == "create", f"{name}: a run must never rewrite or blank it"
        assert node["spec"]["secretType"] == "environment", f"{name}: must never reach a build"


def test_the_addon_link_group_is_rewritten_by_runs_but_runtime_only():
    node = groups(template())["learn-dbops"]
    assert node["updateMode"] == "put", "holds addon links only, nothing generated to clobber"
    assert node["spec"]["secretType"] == "environment"
    assert node["spec"].get("addonDependencies"), "its Postgres admin login comes from addon links"


def test_learn_config_keeps_build_access_and_holds_no_secret():
    node = groups(template())["learn-config"]
    assert (node["updateMode"], node["spec"]["secretType"]) == ("put", "environment-arguments")
    variables = node["spec"]["secrets"]["variables"]
    assert [k for k in variables if SECRET_NAME.search(k)] == []
    assert {k for k in variables if k.startswith("NEXT_PUBLIC_")} >= {
        "NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL", "NEXT_PUBLIC_LEARNHOUSE_MEDIA_URL", "NEXT_PUBLIC_LEARNHOUSE_DOMAIN",
        "NEXT_PUBLIC_LEARNHOUSE_TOP_DOMAIN", "NEXT_PUBLIC_LEARNHOUSE_HTTPS", "NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG"}


def test_no_public_web_value_sits_in_a_runtime_only_group():
    for name, node in groups(template()).items():
        if node["spec"]["secretType"] == "environment":
            assert not [k for k in node["spec"]["secrets"]["variables"] if k.startswith("NEXT_PUBLIC_")], name


def test_every_secret_named_value_is_runtime_only():
    for name, node in groups(template()).items():
        secret_named = [k for k in node["spec"]["secrets"]["variables"] if SECRET_NAME.search(k)]
        if secret_named:
            assert node["spec"]["secretType"] == "environment", (name, secret_named)


def test_no_build_reads_a_runtime_only_value():
    """Builds see only the ARGs their Dockerfile declares; none of these is declared."""
    runtime_only = {k for node in groups(template()).values() if node["spec"]["secretType"] == "environment"
                    for k in node["spec"]["secrets"]["variables"]}
    for dockerfile in (ROOT / "apps/api/Dockerfile", ROOT / "apps/web/Dockerfile"):
        args = set(re.findall(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)", dockerfile.read_text(), re.M))
        assert args and not (args & runtime_only), (dockerfile, args & runtime_only)


def test_generated_secrets_feed_only_create_mode_groups():
    """${fn.randomSecret} is drawn on every run; in a create group it lands only when the group is new."""
    t = template()
    generated = {k for k, v in t["arguments"].items() if str(v).startswith("${fn.randomSecret(")}
    assert generated == set(deploy.SECRET_ARGS)
    text_by_node = [(n, json.dumps(n["spec"])) for n in deploy.nodes(t["spec"]) if n["kind"] != "Workflow"]
    for arg in generated:
        users = [n for n, text in text_by_node if "${args.%s}" % arg in text]
        assert users, arg
        for node in users:
            assert node["kind"] == "SecretGroup" and node["updateMode"] == "create", (arg, node["spec"].get("name"))


def test_the_release_guard_passes_the_template():
    assert guard_problems(template()) == ""


def test_the_release_guard_refuses_a_secret_group_a_run_would_rewrite():
    t = template()
    t2 = copy.deepcopy(t)
    for node in deploy.nodes(t2["spec"]):
        if node.get("kind") == "SecretGroup" and node["spec"]["name"] == "learn-secrets":
            node["updateMode"] = "put"
    assert "learn-secrets must be updateMode create" in guard_problems(t2)


def test_the_release_guard_refuses_a_secret_group_builds_can_read():
    t = template()
    for node in deploy.nodes(t["spec"]):
        if node.get("kind") == "SecretGroup" and node["spec"]["name"] == "learn-storage":
            node["spec"]["secretType"] = "environment-arguments"
    out = guard_problems(t)
    assert "learn-storage must be updateMode create, secretType environment" in out
    assert "learn-storage reaches builds and holds secrets: ['S3_ACCESS_KEY', 'S3_SECRET_KEY']" in out


def test_the_release_guard_refuses_a_secret_in_learn_config():
    t = template()
    for node in deploy.nodes(t["spec"]):
        if node.get("kind") == "SecretGroup" and node["spec"]["name"] == "learn-config":
            node["spec"]["secrets"]["variables"]["LEARNHOUSE_AUTH_JWT_SECRET_KEY_PREVIOUS"] = "x"
    assert "learn-config reaches builds and holds secrets" in guard_problems(t)


def test_the_release_guard_refuses_a_group_with_no_rule():
    t = template()
    for node in deploy.nodes(t["spec"]):
        if node.get("kind") == "SecretGroup" and node["spec"]["name"] == "learn-dbops":
            node["spec"]["name"] = "learn-new"
    assert "settings group learn-new has no rule" in guard_problems(t)


def test_the_guard_and_the_template_agree_on_every_group():
    t = template()
    for name, node in groups(t).items():
        assert deploy.GROUP_RULES[name] == (node["updateMode"], node["spec"]["secretType"]), name


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
