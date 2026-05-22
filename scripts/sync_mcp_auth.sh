#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
container_name="${NOTEBOOKLM_MCP_CONTAINER:-notebooklm-mcp}"

printf 'Syncing MCP auth mirror through docker compose...\n'
docker compose -f "$repo_root/docker-compose.yml" up --no-deps --force-recreate --no-build \
  mcp-auth-sync

if docker container inspect "$container_name" >/dev/null 2>&1; then
  printf 'Checking %s auth mirror...\n' "$container_name"
  docker exec "$container_name" sh -lc 'notebooklm auth check --test --json' >/dev/null
  printf 'MCP container auth OK. The server will reload auth before the next tool call.\n'
else
  printf 'Container %s is not running yet. Start it with: docker compose up -d --build\n' \
    "$container_name"
fi
