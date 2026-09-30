# Otii Learn

Otii Learn is LearnHouse, run by otii. This repo is our copy (fork) of LearnHouse.
Everything otii added lives in `otii/`, `apps/api/src/otii/` and `apps/web/otii/`.
`otii/fork-changes.sh` lists every LearnHouse file we edited (8 files, 38 lines).

## What it does
- Staff sign in to otii once. Otii Learn opens inside otii with no second login (otii's Keycloak).
- It looks like otii: fonts, colours, logo, buttons.
- When someone finishes a course, otii records it in their Otii Learn log. Never in the SCR.

## Start from scratch on a laptop
You need Docker, Node 22, uv 0.12 or newer, and the otii repo running locally (its Keycloak and Postgres).

1. `otii/local/init-env.sh` makes `otii/env/local.env` with fresh secrets. Rerun it after pulling; it adds new settings.
2. `otii/local/up.sh` starts Otii Learn on http://localhost:3100 and runs the setup steps.
3. In the otii repo: `OTII_BACKEND_ENV_FILE=<otii>/backend/.env OTII_LEARN_ENV_FILE=<this repo>/otii/env/local.env scripts/otii-learn/local-up.sh`
   starts otii on http://localhost:5176 with Otii Learn in the menu.
4. `otii/local/demo-course.sh` adds a small demo course (`--remove` deletes it).
5. Sign in to otii, open Otii Learn from the menu.

To see it recorded: in the otii repo, `node scripts/otii-learn/record-journey.mjs <folder>` (settings at the top of the file).

## Stop and undo
- `otii/local/down.sh` stops Otii Learn, keeps data. `--wipe` also undoes the setup steps and deletes its database.
- otii repo: `scripts/otii-learn/local-down.sh` stops otii's side. `--wipe` removes the Keycloak client, the organisation link and its database.

## Setup steps (same on laptop, staging, production)
Run from `apps/api` with the environment's settings:
- `python -m src.otii.setup database` creates Otii Learn's database and login (before the API exists).
- `python -m src.otii.setup all` runs: migrate, webhook to otii, otii branding, features off.
- Add `--remove` to undo (the database undo also needs `OTII_LEARN_ALLOW_DROP=yes`).

On otii's side (otii repo, run where otii runs):
- `python -m app.scripts.keycloak_setup` creates the `otii-learn` Keycloak client when `OTII_LEARN_PUBLIC_URL` and `OTII_LEARN_KEYCLOAK_CLIENT_SECRET` are set. `--remove-otii-learn-client` undoes it.
- `python -m app.scripts.learn_link_org --tier otii --publisher Otii` tells otii which Otii Learn organisation is Otii. `--remove` undoes it.

## Staging or production from scratch (Northflank, inside an otii project)
Everything runs in our own cluster. Nothing here touches otii's services.

1. Shared Redis, once per project (otii repo): `python3 infra/northflank/shared-redis/deploy.py staging`.
   Undo: `... --remove --confirm-delete-redis` (deletes every app's Redis data).
2. Web address (this repo): `python3 otii/northflank/subdomain.py staging create`, then add the CNAME it prints
   at the DNS provider, then `... staging verify`.
3. Release: `python3 otii/northflank/deploy.py staging`. It checks the guards, the cluster room and that
   the commit is pushed, then builds both images from that commit, creates the database, starts the API,
   runs the setup steps and starts the web app. Rerun it for every release.
4. Routes, once the services exist: `python3 otii/northflank/subdomain.py staging routes`.
5. otii's side (otii repo, after its PR is on staging): run the `kc-bootstrap` job (creates the `otii-learn`
   Keycloak client; its release workflow pins the job to the released build) and
   `python -m app.scripts.learn_link_org --tier otii --publisher Otii` in a backend job.

Secrets (database password, session key, admin password, Keycloak client secret, webhook secret) are drawn by
Northflank on the first release and kept on the stored template, so later releases never change them. Two of
them are read by otii (the Keycloak client secret and the webhook secret): after the first release, and after
any release the script reports as having drawn them again, run `kc-bootstrap` and release or restart otii's
backend so both sides hold the same values.

Production is the same with `production` and `--confirm-production`, only on an explicit go-ahead.
Undo on Northflank: `python3 otii/northflank/remove.py staging --confirm-remove` (add `--drop-data` to also
delete the database), then `python3 otii/northflank/subdomain.py staging remove`.

## Where files go
- Laptop: a folder on disk, as LearnHouse does by default.
- Staging and production: otii's own private bucket, in the folder `<environment>/otii-learn/`
  (`OTII_LEARN_S3_KEY_PREFIX`). otii's files sit in `<environment>/<organisation number>/`, so the two never meet.
- Otii Learn uses otii's bucket key. It gets it because `learn-api` and `learn-setup` carry otii's environment
  label (`staging` / `production`), which also gives them every other otii setting of that environment.
  `learn-web` does not carry it. The deploy guards refuse any other workload with that label.
- The folder decides where Otii Learn writes. It does not stop the key reaching the rest of the bucket.
- Videos play through a short-lived signed link to the one file; the bucket stays private.
- The disk Otii Learn used before (`learn-content`) is deleted with
  `python3 otii/northflank/retire_volume.py staging learn-content --confirm-delete`. It refuses if any file is on it.

## Settings
All settings are listed with comments in `otii/env/local.env.example`. Staging and production use the same names, set in Northflank.
Shared with otii (same value both sides): `OTII_LEARN_PUBLIC_URL`, `OTII_LEARN_ORG_SLUG`, `OTII_LEARN_KEYCLOAK_CLIENT_SECRET`, `OTII_LEARN_WEBHOOK_SECRET`.

## Taking LearnHouse updates
`git fetch upstream && git merge upstream/dev`, then check the files `otii/fork-changes.sh` lists.
LearnHouse is AGPL-3.0: people using our version must be able to get its source.

## Not done yet
Production deployment, customer and marketplace organisations, assignments, reminders, document upload, catalogue across organisations.
