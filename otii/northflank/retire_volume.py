#!/usr/bin/env python3
"""Delete a disk Otii Learn no longer uses (learn-content, since files moved to otii's bucket).

    python3 otii/northflank/retire_volume.py staging learn-content --confirm-delete

Refuses when the template still creates the disk, or when any file is on it
(counted inside the service it is attached to). Then detaches it (Northflank
restarts that service) and deletes it, and reads it back to prove it is gone.
Running it again when the disk is already gone does nothing.

Undo: nothing is lost (the disk is empty). To go back to files on a disk,
revert the template commit that moved them to the bucket and run deploy.py:
the template creates the disk again. Production also needs --confirm-production.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from nf import call, die, token, unwrap  # noqa: E402

RETIRABLE = ("learn-content",)


def count_files(project: str, service: str, path: str) -> int:
    marker = "OTII_FILE_COUNT="
    out = subprocess.run(
        ["northflank", "exec", "service", "--projectId", project, "--serviceId", service,
         "--cmd", f"sh -c 'echo {marker}$(find {path} -type f | wc -l)'"],
        capture_output=True, text=True, timeout=180,
    )
    for line in out.stdout.splitlines():
        if marker in line:
            return int(line.split(marker, 1)[1].strip())
    die(f"could not count the files on the disk (exit {out.returncode}); nothing was changed")
    return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env", choices=["staging", "production"])
    parser.add_argument("volume", choices=RETIRABLE)
    parser.add_argument("--confirm-delete", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args()
    if not args.confirm_delete:
        die(f"this deletes the disk {args.volume}; add --confirm-delete")
    if args.env == "production" and not args.confirm_production:
        die("production needs --confirm-production, given only on an explicit go-ahead")
    if f'"name": "{args.volume}"' in (HERE / "template.json").read_text():
        die(f"template.json still creates {args.volume}; the next deploy would bring it back")

    project = {"staging": "otii-staging", "production": "otii"}[args.env]
    path = f"/projects/{project}/volumes/{args.volume}"
    tok = token()
    code, body = call(tok, "GET", path)
    if code == 404:
        print(f"ok  {args.volume} is already gone")
        return 0
    if code >= 300:
        die(f"reading {args.volume} failed with HTTP {code}")
    volume = unwrap(body)
    attached = volume.get("attachedObjects") or []
    mounts = volume.get("mounts") or []

    for obj in attached:
        if obj["type"] != "service":
            die(f"{args.volume} is attached to {obj['type']} {obj['id']}; this script only handles services")
        for mount in mounts:
            files = count_files(project, obj["id"], mount["containerMountPath"])
            print(f"    files on {args.volume} (seen from {obj['id']}): {files}")
            if files:
                die(f"{args.volume} still holds {files} files; nothing was changed")

    for obj in attached:
        code, body = call(tok, "POST", f"{path}/detach", {"nfObject": {"id": obj["id"], "type": obj["type"]}})
        if code >= 300:
            die(f"detaching from {obj['id']} failed with HTTP {code}: {json.dumps(body)[:300]}")
        print(f"ok  detached from {obj['id']} (Northflank restarts it)")
    for _ in range(30):
        code, body = call(tok, "GET", path)
        if code < 300 and not unwrap(body).get("attachedObjects"):
            break
        time.sleep(10)
    else:
        die(f"{args.volume} still shows as attached after 5 minutes; not deleted")

    code, body = call(tok, "DELETE", path)
    if code >= 300 and code != 404:
        die(f"deleting {args.volume} failed with HTTP {code}: {json.dumps(body)[:300]}")
    for _ in range(30):
        code, _ = call(tok, "GET", path)
        if code == 404:
            print(f"ok  {args.volume} deleted")
            return 0
        time.sleep(10)
    die(f"{args.volume} was asked to delete but still reads back after 5 minutes")
    return 1


if __name__ == "__main__":
    sys.exit(main())
