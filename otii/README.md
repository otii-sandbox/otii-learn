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
- `python -m src.otii.setup all` sets up: the webhook to otii, and otii branding.
- `python -m src.otii.setup all --remove` undoes both.

On otii's side (otii repo, run where otii runs):
- `python -m app.scripts.keycloak_setup` creates the `otii-learn` Keycloak client when `OTII_LEARN_PUBLIC_URL` and `OTII_LEARN_KEYCLOAK_CLIENT_SECRET` are set. `--remove-otii-learn-client` undoes it.
- `python -m app.scripts.learn_link_org --tier otii --publisher Otii` tells otii which Otii Learn organisation is Otii. `--remove` undoes it.

## Settings
All settings are listed with comments in `otii/env/local.env.example`. Staging and production use the same names, set in Northflank.
Shared with otii (same value both sides): `OTII_LEARN_PUBLIC_URL`, `OTII_LEARN_ORG_SLUG`, `OTII_LEARN_KEYCLOAK_CLIENT_SECRET`, `OTII_LEARN_WEBHOOK_SECRET`.

## Taking LearnHouse updates
`git fetch upstream && git merge upstream/dev`, then check the files `otii/fork-changes.sh` lists.
LearnHouse is AGPL-3.0: people using our version must be able to get its source.

## Not done yet (UNVERIFIED on Northflank)
Staging and production deployment (template, images, Redis, bucket, email), customer and marketplace organisations, assignments, reminders, document upload, catalogue across organisations.
