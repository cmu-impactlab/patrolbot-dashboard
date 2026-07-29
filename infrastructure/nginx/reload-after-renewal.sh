#!/bin/sh

set -eu

script_path="$(readlink -f "$0")"
nginx_dir="$(dirname "$script_path")"
repo_root="$(CDPATH= cd -- "$nginx_dir/../.." && pwd)"

exec docker compose \
  --env-file "$repo_root/infrastructure/.env" \
  -f "$repo_root/infrastructure/docker-compose.production.yml" \
  exec -T nginx nginx -s reload
