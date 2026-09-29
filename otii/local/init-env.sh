#!/usr/bin/env bash
# Create otii/env/local.env from the example, generating each "generate" value.
# Safe to rerun: existing values are never changed; settings added to the
# example since the file was made are appended (generated where needed).
set -euo pipefail
cd "$(dirname "$0")/../.."

example=otii/env/local.env.example
target=otii/env/local.env
fill() {
  local line=$1
  if [[ "$line" == *=generate ]]; then
    echo "${line%=generate}=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  else
    echo "$line"
  fi
}

if [ ! -f "$target" ]; then
  while IFS= read -r line; do fill "$line"; done < "$example" > "$target"
  chmod 600 "$target"
  echo "created $target"
  exit 0
fi

added=0
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Z0-9_]+)= ]] || continue
  if ! grep -q "^${BASH_REMATCH[1]}=" "$target"; then
    fill "$line" >> "$target"
    added=$((added + 1))
  fi
done < "$example"
echo "$target: $added new setting(s) added"
