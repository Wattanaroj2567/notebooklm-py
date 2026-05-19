#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
container_name="${NOTEBOOKLM_MCP_CONTAINER:-notebooklm-mcp}"

printf 'Syncing MCP auth mirror through docker compose...\n'
docker compose -f "$repo_root/docker-compose.yml" up --no-deps --force-recreate --no-build \
  mcp-auth-sync

if docker container inspect "$container_name" >/dev/null 2>&1; then
  printf 'Recreating %s to apply the workspace auth mount...\n' "$container_name"
  docker compose -f "$repo_root/docker-compose.yml" up -d --no-deps --force-recreate --no-build \
    notebooklm-mcp >/dev/null
  printf 'Checking container auth...\n'
  docker exec "$container_name" sh -lc 'notebooklm auth check --test --json' >/dev/null
  printf 'MCP container auth OK.\n'
else
  printf 'Container %s is not running yet. Start it with: docker compose up -d --build\n' \
    "$container_name"
fi
