#!/usr/bin/env bash
# Start Otii Learn on a laptop: database, Redis, API, web, then the setup steps.
# Safe to rerun. Stop with otii/local/down.sh (add --wipe to delete all data).
set -euo pipefail
cd "$(dirname "$0")/../.."
root=$(pwd)
state="$root/otii/.state"
mkdir -p "$state"

[ -f otii/env/local.env ] || { echo "run otii/local/init-env.sh first"; exit 1; }
set -a; . otii/env/local.env; set +a

running() { [ -f "$state/$1.pid" ] && kill -0 "$(cat "$state/$1.pid")" 2>/dev/null; }

echo "1/5 database and Redis"
docker compose -f otii/local/compose.yml up -d --wait

echo "2/5 API dependencies"
(cd apps/api && uv sync --frozen --quiet)

echo "3/5 API"
if ! running api; then
  (cd apps/api && exec nohup .venv/bin/uvicorn app:app --host 127.0.0.1 --port "$LEARN_API_PORT") \
    > "$state/api.log" 2>&1 < /dev/null &
  echo $! > "$state/api.pid"
fi
curl -sf --retry 60 --retry-connrefused --retry-delay 2 -o /dev/null "http://127.0.0.1:$LEARN_API_PORT/api/v1/health"

echo "4/5 setup steps"
(cd apps/api && .venv/bin/python -m src.otii.setup all)

echo "5/5 web"
if [ ! -x otii/.tools/node_modules/.bin/bun ]; then
  npm install --silent --prefix otii/.tools bun@1.4.2
fi
bun="$root/otii/.tools/node_modules/.bin/bun"
(cd apps/web && "$bun" install --frozen-lockfile >/dev/null)
if ! running web; then
  (cd apps/web && exec nohup "$bun" run next dev --turbopack -p "$LEARN_WEB_PORT") \
    > "$state/web.log" 2>&1 < /dev/null &
  echo $! > "$state/web.pid"
fi
curl -sf --retry 90 --retry-connrefused --retry-delay 2 --max-time 300 -o /dev/null "http://localhost:$LEARN_WEB_PORT/"

echo "Otii Learn is up: $OTII_LEARN_PUBLIC_URL (logs in otii/.state)"
