#!/usr/bin/env python3
"""Otii Learn's web address on Northflank, with path routing. Safe to rerun.

    python3 otii/northflank/subdomain.py staging create   # register; prints the DNS record to add
    python3 otii/northflank/subdomain.py staging verify   # after the DNS record exists
    python3 otii/northflank/subdomain.py staging routes   # after the services exist
    python3 otii/northflank/subdomain.py staging remove   # undo: delete the subdomain

Routes copy LearnHouse's own web server (docker/nginx.conf): /api/v1 and
/content go to the API, everything else to the web app.
Settings come from otii/northflank/<env>-arguments.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nf import call, die, token, unwrap  # noqa: E402

HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("create", "verify", "routes", "remove"):
        die(__doc__)
    env, action = argv
    args = json.loads((HERE / f"{env}-arguments.json").read_text())
    domain, sub = args["domain"], args["subdomain"]
    tok = token()
    base = f"/domains/{domain}/subdomains"

    if action == "create":
        code, body = call(tok, "GET", f"{base}/{sub}")
        if code == 404:
            code, body = call(tok, "POST", base, {"subdomain": sub, "routingMode": "paths"})
            if code >= 300:
                die(f"adding {sub}.{domain} failed with HTTP {code}: {json.dumps(body)[:400]}")
            code, body = call(tok, "GET", f"{base}/{sub}")
        s = unwrap(body)
        print(f"{s.get('fullName')}: verified={s.get('verified')}")
        print(f"DNS record to add at the domain's DNS provider: {s.get('recordType')} {sub} -> {s.get('content')}")
        return 0

    if action == "verify":
        code, body = call(tok, "POST", f"{base}/{sub}/verify")
        if code >= 300:
            die(f"verifying {sub}.{domain} failed with HTTP {code}: {json.dumps(body)[:400]}")
        print(f"ok  {sub}.{domain} verified")
        return 0

    if action == "routes":
        project = args["projectName"]
        routes = [
            ("api", "prefix", "/api/v1", args["apiService"], args["apiPort"]),
            ("content", "prefix", "/content", args["apiService"], args["apiPort"]),
            ("web", "prefix", "/", args["webService"], args["webPort"]),
        ]
        code, body = call(tok, "GET", f"{base}/{sub}/paths")
        existing = {p.get("uri"): p for p in (unwrap(body).get("paths", []) if code < 300 else [])}
        for name, mode, uri, service, port in routes:
            path = existing.get(uri)
            if path is None:
                code, body = call(tok, "POST", f"{base}/{sub}/paths", {"mode": mode, "uri": uri})
                if code >= 300:
                    die(f"adding path {uri} failed with HTTP {code}: {json.dumps(body)[:400]}")
                path = unwrap(body)
            path_id = path.get("id") or path.get("name")
            code, body = call(tok, "POST", f"{base}/{sub}/paths/{path_id}/assign",
                              {"assignment": {"projectId": project, "serviceId": service, "portName": port}})
            if code >= 300:
                die(f"routing {uri} to {service}:{port} failed with HTTP {code}: {json.dumps(body)[:400]}")
            print(f"ok  {sub}.{domain}{uri} -> {service}:{port}")
        return 0

    code, body = call(tok, "DELETE", f"{base}/{sub}")
    if code >= 300 and code != 404:
        die(f"deleting {sub}.{domain} failed with HTTP {code}: {json.dumps(body)[:400]}")
    print(f"ok  {sub}.{domain} {'deleted' if code < 300 else 'was not there'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
