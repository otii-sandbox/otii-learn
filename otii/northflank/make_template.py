#!/usr/bin/env python3
"""Writes otii/northflank/template.json. Edit here, rerun, commit both.

Otii Learn inside an existing otii project. Creates only learn-* resources
(plus the otii-learn-shared settings group otii's staging workloads also read),
never the project, the Postgres server or the Redis server.
"""

from __future__ import annotations

import json
from pathlib import Path

TAG = "otii-learn"
CONTENT_DIR = "/app/content"  # LearnHouse's upload folder inside the API image


def plan(ref, name, kind, cpu_limit, mem_limit, cpu_request=None, mem_request=None):
    if kind == "build":
        config = {"type": "northflank", "resources": {
            "cpu": {"resources": {"limit": cpu_limit}},
            "memory": {"resources": {"limit": mem_limit}}}}
        types = ["build"]
    else:
        config = {"type": "kubernetes", "resources": {
            "cpu": {"resources": {"limit": cpu_limit, "request": cpu_request}},
            "memory": {"resources": {"limit": mem_limit, "request": mem_request}}}}
        types = ["deployment"]
    return {"kind": "CustomPlan", "ref": ref, "spec": {"name": name, "type": types, "configuration": config},
            "updateMode": "put"}


def group(ref, name, description, variables, tags=None, jobs=None, addon=None, priority=10):
    spec = {
        "name": name,
        "description": description,
        "type": "secret",
        "secretType": "environment-arguments",
        "priority": priority,
        "restrictions": {"restricted": True, "nfObjects": [{"id": j, "type": "job"} for j in (jobs or [])],
                         "tags": tags or [], "stageIds": []},
        "secrets": {"variables": variables, "files": {}},
    }
    if addon:
        spec["addonDependencies"] = addon
    return {"kind": "SecretGroup", "ref": ref, "spec": spec, "updateMode": "put"}


def build_service(ref, name, dockerfile, context):
    return {"kind": "BuildService", "ref": ref, "spec": {
        "name": name,
        "description": f"Builds {name.removesuffix('-build')} from the Otii Learn fork. Only this template starts builds.",
        "tags": [TAG],
        "billing": {"buildPlan": "${refs.buildPlan.id}"},
        "vcsData": {"projectUrl": "${args.repoUrl}", "projectType": "github"},
        "buildSettings": {"dockerfile": {"buildEngine": "buildkit", "dockerFilePath": dockerfile,
                                         "dockerWorkDir": context}},
        "buildConfiguration": {"branchRestrictions": ["${args.gitBranch}"], "pathIgnoreRules": [],
                               "isAllowList": False, "ciIgnoreFlagsEnabled": False, "prRestrictions": []},
        "disabledCI": True,
        "buildArguments": {},
    }, "updateMode": "put"}


def build_run(ref, service_ref):
    return {"kind": "Build", "ref": ref, "condition": "success", "spec": {
        "id": f"${{refs.{service_ref}.id}}", "type": "service", "branch": "${args.gitBranch}",
        "reuseExistingBuilds": True, "sha": "${args.releaseSha}"}}


def shell(command: str) -> dict:
    """LearnHouse's API image has an ENTRYPOINT that always starts the API and
    ignores any command (found on staging 29 Sep 2026: the db job started the
    API instead). So the entrypoint itself is replaced with the shell."""
    assert command.startswith("sh -c '") and command.endswith("'"), command
    return {"configType": "customEntrypointCustomCommand", "customEntrypoint": "/bin/sh",
            "customCommand": command[len("sh "):]}


def internal(build_ref, run_ref):
    return {"id": f"${{refs.{build_ref}.id}}", "branch": "${args.gitBranch}", "buildId": f"${{refs.{run_ref}.id}}"}


def job(ref, name, description, command, run_ref="apiBuildRun", deadline=600):
    return {"kind": "ManualJob", "ref": ref, "spec": {
        "name": name, "description": description, "tags": [TAG],
        "billing": {"deploymentPlan": "${refs.jobPlan.id}"},
        "deployment": {"internal": internal("apiBuild", run_ref),
                       "docker": shell(command)},
        "runtimeEnvironment": {}, "backoffLimit": 0, "runOnSourceChange": "never",
        "activeDeadlineSeconds": deadline,
        "buildConfiguration": {"pathIgnoreRules": [], "isAllowList": False, "ciIgnoreFlagsEnabled": False},
        "buildArguments": {},
    }, "updateMode": "put"}


def health(path, port):
    return [
        {"protocol": "HTTP", "type": "startupProbe", "path": path, "port": port,
         "initialDelaySeconds": 5, "periodSeconds": 10, "timeoutSeconds": 3, "failureThreshold": 30},
        {"protocol": "HTTP", "type": "readinessProbe", "path": path, "port": port,
         "initialDelaySeconds": 1, "periodSeconds": 10, "timeoutSeconds": 3, "failureThreshold": 3,
         "successThreshold": 1},
        {"protocol": "HTTP", "type": "livenessProbe", "path": path, "port": port,
         "initialDelaySeconds": 1, "periodSeconds": 15, "timeoutSeconds": 5, "failureThreshold": 8},
    ]


def service(ref, name, description, plan_ref, build_ref, run_ref, port, health_path, command=None):
    docker = shell(command) if command else {"configType": "default"}
    return {"kind": "DeploymentService", "ref": ref, "spec": {
        "name": name, "description": description, "tags": [TAG],
        "billing": {"deploymentPlan": f"${{refs.{plan_ref}.id}}"},
        "deployment": {"instances": 1, "docker": docker, "internal": internal(build_ref, run_ref),
                       "containerSnapshot": {"capture": {"onTermination": False, "retention": {"maxSnapshots": 10}}}},
        "runtimeEnvironment": {},
        "ports": [{"name": "p01", "internalPort": port, "public": True, "protocol": "HTTP",
                   "security": {"credentials": [], "policies": [], "sso": {}}, "domains": []}],
        "healthChecks": health(health_path, port),
    }, "updateMode": "put"}


# LearnHouse wants whole connection strings; Northflank links only the Redis
# password. They are put together at start, from the in-cluster service names
# (never Northflank's addon DNS records: otii outage, 28 Aug 2026).
API_START = (
    "sh -c 'export LEARNHOUSE_SQL_CONNECTION_STRING=\"postgresql+asyncpg://$OTII_LEARN_DB_USER:$OTII_LEARN_DB_PASSWORD"
    "@$OTII_LEARN_DB_HOST:5432/$OTII_LEARN_DB_NAME\" "
    "LEARNHOUSE_REDIS_CONNECTION_STRING=\"redis://:$REDIS_PASSWORD@$OTII_LEARN_REDIS_HOST:6379/$OTII_LEARN_REDIS_DB\"; "
    "exec %s'"
)

spec_steps = [
    {"kind": "Condition", "spec": {"kind": "Addon", "spec": {"data": {"addonId": "${args.dbAddonId}"}, "type": "running"}}},
    {"kind": "Condition", "spec": {"kind": "Addon", "spec": {"data": {"addonId": "${args.redisAddonId}"}, "type": "running"}}},
    group("learnConfig", "learn-config", "Otii Learn settings. Tagged otii-learn only.", {
        "LEARNHOUSE_DEVELOPMENT_MODE": "false",
        "LEARNHOUSE_TENANCY": "single",
        "LEARNHOUSE_DOMAIN": "${args.domain}",
        "LEARNHOUSE_FRONTEND_DOMAIN": "${args.domain}",
        "LEARNHOUSE_SSL": "true",
        "LEARNHOUSE_ALLOWED_ORIGINS": "${args.publicUrl}",
        "LEARNHOUSE_PGBOUNCER": "true",
        "PGSSLMODE": "require",
        "LEARNHOUSE_EMAIL_PROVIDER": "smtp",
        "LEARNHOUSE_SMTP_HOST": "${args.smtpHost}",
        "LEARNHOUSE_SMTP_PORT": "${args.smtpPort}",
        "LEARNHOUSE_SYSTEM_EMAIL_ADDRESS": "${args.systemEmail}",
        "LEARNHOUSE_INITIAL_ORG_NAME": "Otii",
        "LEARNHOUSE_INITIAL_ORG_SLUG": "${args.orgSlug}",
        "LEARNHOUSE_INITIAL_ADMIN_EMAIL": "${args.adminEmail}",
        "OTII_LEARN_DB_HOST": "${args.dbHost}",
        "OTII_LEARN_DB_NAME": "${args.dbName}",
        "OTII_LEARN_DB_USER": "${args.dbUser}",
        "OTII_LEARN_REDIS_HOST": "${args.redisHost}",
        "OTII_LEARN_REDIS_DB": "${args.redisDb}",
        "OTII_KEYCLOAK_URL": "${args.keycloakUrl}",
        "OTII_KEYCLOAK_INTERNAL_URL": "${args.keycloakInternalUrl}",
        "OTII_KEYCLOAK_REALM": "${args.keycloakRealm}",
        "OTII_LEARN_WEBHOOK_URL": "${args.otiiWebhookUrl}",
        "OTII_TRUSTED_WEBHOOK_HOSTS": "${args.trustedWebhookHosts}",
        "OTII_LEARN_FRAME_ANCESTORS": "${args.frameAncestors}",
        "OTII_LEARN_THEME": "otii",
        "OTII_LEARN_BRAND_COLOR": "#1C300A",
        "OTII_LEARN_BRAND_FONT": "Baloo 2",
        "OTII_LEARN_BRAND_LOGO": "src/otii/brand/otii-logo-orange.png",
        "OTII_LEARN_DISABLED_FEATURES": "${args.disabledFeatures}",
        "NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL": "${args.publicUrl}/",  # browsers use it; /api/v1 routes to learn-api
        "NEXT_PUBLIC_LEARNHOUSE_MEDIA_URL": "${args.publicUrl}/",
        "NEXT_PUBLIC_LEARNHOUSE_DOMAIN": "${args.domain}",
        "NEXT_PUBLIC_LEARNHOUSE_TOP_DOMAIN": "${args.topDomain}",
        "NEXT_PUBLIC_LEARNHOUSE_HTTPS": "true",
        "NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG": "${args.orgSlug}",
        "NEXT_TELEMETRY_DISABLED": "1",
    }, tags=[TAG]),
    group("learnSecrets", "learn-secrets", "Otii Learn secrets, generated and stored by Northflank. Tagged otii-learn only.", {
        "LEARNHOUSE_AUTH_JWT_SECRET_KEY": "${args.jwtSecret}",
        "COLLAB_INTERNAL_KEY": "${args.collabKey}",
        "LEARNHOUSE_INITIAL_ADMIN_PASSWORD": "${args.adminPassword}",
        "OTII_LEARN_DB_PASSWORD": "${args.dbPassword}",
    }, tags=[TAG], addon=[{"addonId": "${args.redisAddonId}",
                           "keys": [{"keyName": "PASSWORD", "aliases": ["REDIS_PASSWORD"]}]}]),
    group("learnShared", "otii-learn-shared",
          "Values both Otii Learn and otii read: its address, organisation and the two shared secrets.", {
              "OTII_LEARN_PUBLIC_URL": "${args.publicUrl}",
              "OTII_LEARN_ORG_SLUG": "${args.orgSlug}",
              "OTII_LEARN_API_URL": "http://learn-api:9000",
              "OTII_LEARN_KEYCLOAK_CLIENT_SECRET": "${args.kcClientSecret}",
              "OTII_LEARN_WEBHOOK_SECRET": "${args.webhookSecret}",
          }, tags=[TAG, "${args.otiiEnvTag}"]),
    build_service("apiBuild", "learn-api-build", "/apps/api/Dockerfile", "/apps/api"),
    build_run("apiBuildRun", "apiBuild"),
    build_service("webBuild", "learn-web-build", "/apps/web/Dockerfile", "/apps/web"),
    build_run("webBuildRun", "webBuild"),
    job("dbProvisionJob", "learn-db-provision",
        "Creates Otii Learn's own database, login and pgvector on the existing Postgres. Idempotent.",
        "sh -c 'exec .venv/bin/python -m src.otii.setup database'", deadline=300),
    group("learnDbops", "learn-dbops",
          "Postgres admin login for learn-db-provision only. Its host is never used.", {
              "PGHOST": "${args.dbHost}",
          }, jobs=["learn-db-provision"], priority=20, addon=[{"addonId": "${args.dbAddonId}", "keys": [
              {"keyName": "POSTGRES_URI", "aliases": ["PG_ADMIN_URI"]},
              {"keyName": "PORT", "aliases": ["PGPORT"]},
              {"keyName": "ADMIN_USERNAME", "aliases": ["PGUSER"]},
              {"keyName": "ADMIN_PASSWORD", "aliases": ["PGPASSWORD"]}]}]),
    {"kind": "JobRun", "ref": "dbProvisionRun", "condition": "success", "spec": {"jobId": "${refs.dbProvisionJob.id}"}},
    service("apiService", "learn-api", "Otii Learn API (LearnHouse). Single copy: uploads live on its volume.",
            "apiPlan", "apiBuild", "apiBuildRun", 9000, "/api/v1/health",
            # Not ./docker-entrypoint.sh: it binds uvicorn to $HOSTNAME, the
            # pod name, so Northflank's in-pod health check on localhost never
            # answered and every start was killed after 5 minutes (staging,
            # 29 Sep 2026). The API retries its own database connection.
            command=API_START % ".venv/bin/uvicorn app:app --host 0.0.0.0 --port 9000 --timeout-keep-alive 600"),
    {"kind": "Volume", "ref": "contentVolume", "spec": {
        "name": "learn-content",
        "mounts": [{"containerMountPath": CONTENT_DIR, "volumeMountPath": ""}],
        "spec": {"storageClassName": "ssd", "storageSize": "${args.contentStorage}", "accessMode": "ReadWriteOnce"},
        "attachedObjects": [{"id": "${refs.apiService.id}", "type": "service"}],
    }, "updateMode": "put"},
    {"kind": "Condition", "spec": {"kind": "Service", "spec": {"data": {"serviceId": "${refs.apiService.id}"}, "type": "running"}}},
    job("setupJob", "learn-setup",
        "Runs after the API is up: migrations, the webhook to otii, otii branding, features off. Idempotent.",
        "sh -c '" + API_START.split("'")[1].replace("exec %s", "exec .venv/bin/python -m src.otii.setup all") + "'"),
    {"kind": "JobRun", "ref": "setupRun", "condition": "success", "spec": {"jobId": "${refs.setupJob.id}"}},
    service("webService", "learn-web", "Otii Learn web app (LearnHouse). The public address routes /api/v1 and /content to learn-api.",
            "webPlan", "webBuild", "webBuildRun", 3000, "/api/health",
            # Same $HOSTNAME trap in the web image's entrypoint (Next.js binds to it).
            command="sh -c 'export HOSTNAME=0.0.0.0; exec ./docker-entrypoint.sh'"),
]

template = {
    "apiVersion": "v1.2",
    "name": "otii-learn",
    "description": "Otii Learn inside an existing otii project. Creates only learn resources, never the project, Postgres or Redis. One file for every environment.",
    "options": {"autorun": False, "concurrencyPolicy": "queue"},
    "arguments": {
        "projectName": "otii-staging",
        "otiiEnvTag": "staging",
        "repoUrl": "https://github.com/otii-sandbox/otii-learn",
        "gitBranch": "otii",
        "releaseSha": "set-per-run",
        "domain": "learn-staging.get-otii.com",
        "topDomain": "get-otii.com",
        "publicUrl": "https://learn-staging.get-otii.com",
        "frameAncestors": "https://staging.get-otii.com",
        "orgSlug": "otii",
        "adminEmail": "learn-admin@otii.com",
        "systemEmail": "learn@get-otii.com",
        "smtpHost": "mailpit",
        "smtpPort": "1025",
        "dbAddonId": "postgres",
        "dbHost": "postgres-postgresql",
        "dbName": "otii_learn",
        "dbUser": "otii_learn",
        "dbPassword": "${fn.randomSecret(32)}",
        "redisAddonId": "redis",
        "redisHost": "redis-master",
        "redisDb": "0",
        "keycloakUrl": "https://auth-staging.get-otii.com",
        "keycloakInternalUrl": "http://keycloak:8080",
        "keycloakRealm": "otii-staging",
        "otiiWebhookUrl": "http://backend:8000/v2/api/learn/webhooks/learnhouse",
        "trustedWebhookHosts": "backend",
        "disabledFeatures": "boards",
        "contentStorage": "5120",
        "jwtSecret": "${fn.randomSecret(48)}",
        "collabKey": "${fn.randomSecret(32)}",
        "adminPassword": "${fn.randomSecret(24)}",
        "kcClientSecret": "${fn.randomSecret(40)}",
        "webhookSecret": "${fn.randomSecret(40)}",
    },
    "spec": {"kind": "Workflow", "spec": {"type": "sequential", "steps": [
        {"kind": "ResourceTag", "ref": "learnTag", "spec": {
            "name": TAG, "description": "Otii Learn workloads. Otii Learn settings attach to this tag only.",
            "useAsInfrastructureLabel": False}, "updateMode": "put"},
        plan("apiPlan", "learn-api", "service", 0.5, 768, 0.1, 384),
        plan("webPlan", "learn-web", "service", 0.5, 512, 0.1, 256),
        plan("jobPlan", "learn-job", "service", 0.2, 384, 0.05, 192),
        plan("buildPlan", "learn-build", "build", 2, 4096),
        {"kind": "Workflow", "spec": {"type": "sequential", "context": {"projectId": "${args.projectName}"},
                                      "steps": spec_steps}},
    ]}},
}

out = Path(__file__).resolve().parent / "template.json"
out.write_text(json.dumps(template, indent=2) + "\n")
print(f"wrote {out}")
