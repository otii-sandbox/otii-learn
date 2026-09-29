#!/usr/bin/env bash
# Stop Otii Learn on a laptop.
#   down.sh          stop everything, keep the data
#   down.sh --wipe   also undo the setup steps and delete the database, Redis and logs
set -euo pipefail
cd "$(dirname "$0")/../.."
state=otii/.state
wipe=false
[ "${1:-}" = "--wipe" ] && wipe=true

[ -f otii/env/local.env ] && { set -a; . otii/env/local.env; set +a; }

if $wipe && docker compose -f otii/local/compose.yml ps --status running -q postgres | grep -q .; then
  echo "undo setup steps"
  (cd apps/api && .venv/bin/python -m src.otii.setup all --remove)
fi

for name in web api; do
  if [ -f "$state/$name.pid" ]; then
    pid=$(cat "$state/$name.pid")
    # The web server starts child processes; stop the whole group.
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    rm -f "$state/$name.pid"
    echo "stopped $name"
  fi
done

if $wipe; then
  docker compose -f otii/local/compose.yml down -v
  rm -rf "$state"
  echo "wiped database, Redis and logs"
else
  docker compose -f otii/local/compose.yml stop
fi
