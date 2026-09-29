"""Step database: Otii Learn's own database and login on a shared Postgres server.

Creates, and nothing else:
  * a login role with no superuser, create-database or create-role rights;
    its password is (re)set every run, so rotating it is rerunning this step;
  * a database owned by that role, which only that role may connect to;
  * the pgvector extension inside it (LearnHouse's course search tables need it).
Never touches any other database or role on the server.

Undo drops the database and the role, which deletes every course and all
progress. It refuses unless OTII_LEARN_ALLOW_DROP=yes is also set.

Settings: OTII_LEARN_DB_ADMIN_URL (server admin login, postgresql://...),
OTII_LEARN_DB_NAME, OTII_LEARN_DB_USER, OTII_LEARN_DB_PASSWORD.
The admin URL's host must be the in-cluster service name, never Northflank's
addon DNS record (otii outage, 28 Aug 2026).
"""

from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql

from src.otii.setup.settings import require

PROTECTED_DATABASES = {"otii", "postgres", "template0", "template1", "keycloak", "otii_pipeline"}
NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def _admin_url() -> str:
    url = require("OTII_LEARN_DB_ADMIN_URL")
    host = urlparse(url).hostname or ""
    if host.endswith((".code.run", ".northflank.com")):
        raise SystemExit("database: admin URL must use the in-cluster service name, not the addon DNS record")
    return url


def _connect(url: str, dbname: str | None = None):
    if dbname:
        parts = urlparse(url)
        url = urlunparse(parts._replace(path=f"/{dbname}"))
    for attempt in range(30):
        try:
            conn = psycopg2.connect(url)
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError:
            if attempt == 29:
                raise
            time.sleep(2)


def run(remove: bool = False) -> str:
    db, role = require("OTII_LEARN_DB_NAME"), require("OTII_LEARN_DB_USER")
    for name in (db, role):
        if not NAME.match(name):
            raise SystemExit(f"database: {name!r} is not a safe name (a-z, 0-9, _)")
    if db in PROTECTED_DATABASES or role in PROTECTED_DATABASES:
        raise SystemExit(f"database: refusing to manage {db}/{role}, which belongs to something else")

    admin = _connect(_admin_url())
    with admin.cursor() as cur:
        if remove:
            if os.environ.get("OTII_LEARN_ALLOW_DROP") != "yes":
                raise SystemExit("database: undo deletes all Otii Learn data; set OTII_LEARN_ALLOW_DROP=yes to confirm")
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
            cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            return f"database: dropped {db} and role {role}"

        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT").format(sql.Identifier(role))
            )
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(require("OTII_LEARN_DB_PASSWORD"))
            )
        )
        cur.execute("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname = %s", (role,))
        if cur.fetchone()[0]:
            raise SystemExit(f"database: role {role} has more rights than it should; stopping")
        # Postgres 16+: the creator gets ADMIN on a new role but not SET, which
        # CREATE DATABASE ... OWNER needs (found on staging, 28 Sep 2026).
        cur.execute("SELECT current_setting('server_version_num')::int >= 160000")
        pg16 = cur.fetchone()[0]
        cur.execute("SELECT pg_has_role(CURRENT_USER, %s, %s)", (role, "SET" if pg16 else "MEMBER"))
        if not cur.fetchone()[0]:
            grant = "GRANT {} TO CURRENT_USER WITH SET TRUE" if pg16 else "GRANT {} TO CURRENT_USER"
            cur.execute(sql.SQL(grant).format(sql.Identifier(role)))
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(role)))
        cur.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)))
        cur.execute(
            sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(role))
        )
    admin.close()

    inside = _connect(_admin_url(), db)
    with inside.cursor() as cur:
        cur.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(role)))
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    inside.close()
    return f"database: {db} ready for {role}, with pgvector"
