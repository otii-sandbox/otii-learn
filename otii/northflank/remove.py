#!/usr/bin/env python3
"""Undo deploy.py: remove Otii Learn from an otii project on Northflank.

    python3 otii/northflank/remove.py staging --confirm-remove
    python3 otii/northflank/remove.py staging --confirm-remove --drop-data   # also delete all courses and progress

Removes, in reverse order: learn-web, learn-setup, the media volume, learn-api,
learn-db-provision, the build services, the learn settings groups and the
stored template. Keeps the database unless --drop-data (then it first runs the
database step's undo as a job, which drops Otii Learn's database and login).
Never touches otii's services, Postgres or the shared Redis. The web address is
removed separately: subdomain.py <env> remove. Production also needs
--confirm-production.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from nf import call, die, token, unwrap, use_team  # noqa: E402

SERVICES = ["learn-web", "learn-api", "learn-api-build", "learn-web-build"]
JOBS = ["learn-setup", "learn-db-provision"]
GROUPS = ["learn-dbops", "otii-learn-shared", "learn-storage", "learn-secrets", "learn-config"]
VOLUMES = ["learn-content"]


def drop_database(tok: str, project: str) -> None:
    base = f"/projects/{project}/jobs/learn-db-provision/runs"
    code, body = call(tok, "POST", base, {
        "runtimeEnvironment": {"OTII_LEARN_ALLOW_DROP": "yes"},
        "deployment": {"docker": {"configType": "customEntrypointCustomCommand", "customEntrypoint": "/bin/sh",
                                  "customCommand": "-c 'exec .venv/bin/python -m src.otii.setup database --remove'"}},
    })
    if code >= 300:
        die(f"starting the database undo failed with HTTP {code}")
    run_id = unwrap(body).get("id")
    for _ in range(60):
        time.sleep(10)
        code, body = call(tok, "GET", f"{base}/{run_id}")
        status = unwrap(body).get("status")
        if status in ("SUCCESS", "FAILED"):
            if status != "SUCCESS":
                die("the database undo job failed; nothing else was removed")
            print("ok  Otii Learn database and login dropped")
            return
    die("the database undo job did not finish in 10 minutes; nothing else was removed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env", choices=["staging", "production"])
    parser.add_argument("--confirm-remove", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    parser.add_argument("--drop-data", action="store_true")
    args = parser.parse_args()
    if not args.confirm_remove:
        die("this removes Otii Learn from Northflank; add --confirm-remove")
    if args.env == "production" and not args.confirm_production:
        die("production needs --confirm-production, given only on an explicit go-ahead")
    project = use_team(args.env)["projectName"]
    tok = token()

    if args.drop_data:
        drop_database(tok, project)

    def delete(kind: str, name: str) -> None:
        code, _ = call(tok, "DELETE", f"/projects/{project}/{kind}/{name}")
        state = "removed" if code < 300 else "was not there" if code == 404 else f"FAILED (HTTP {code})"
        print(f"    {kind[:-1]} {name}: {state}")
        if code >= 300 and code != 404:
            die(f"stopping: could not remove {name}")

    delete("services", "learn-web")
    delete("jobs", "learn-setup")
    for volume in VOLUMES:
        delete("volumes", volume)
    delete("services", "learn-api")
    delete("jobs", "learn-db-provision")
    for service in SERVICES[2:]:
        delete("services", service)
    for group in GROUPS:
        delete("secrets", group)
    code, _ = call(tok, "DELETE", f"/templates/otii-learn-{args.env}")
    print(f"    template otii-learn-{args.env}: {'removed' if code < 300 else 'was not there' if code == 404 else code}")
    print("ok  Otii Learn removed" + ("" if args.drop_data else "; its database was kept (--drop-data deletes it)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
