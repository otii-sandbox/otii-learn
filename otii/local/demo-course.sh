#!/usr/bin/env bash
# Create (or with --remove, delete) the laptop demo course. Otii Learn must be up.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; . otii/env/local.env; set +a
exec apps/api/.venv/bin/python otii/local/demo_course.py "$@"
