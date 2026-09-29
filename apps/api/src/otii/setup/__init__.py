"""Otii Learn setup steps. Every step is idempotent and has an undo.

Run from apps/api with the environment's settings loaded:
    python -m src.otii.setup all            # set everything up
    python -m src.otii.setup all --remove   # undo everything, in reverse order
    python -m src.otii.setup <step> [--remove]

Steps: database, migrate, webhook, branding, features. The Otii Learn Keycloak client is not set up here: otii owns
its realm and creates the client in backend/app/scripts/keycloak_setup.py.
The same command runs on a laptop and as a Northflank job; only the settings
differ (see otii/README.md).
"""
