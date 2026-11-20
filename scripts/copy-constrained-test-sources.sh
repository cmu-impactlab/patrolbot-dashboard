#!/usr/bin/env bash
# Checkout-relative inputs for constrained server tests; no developer artifacts.
set -euo pipefail

source_root="$1"
destination="$2"
mkdir -p "$destination/server" "$destination/infrastructure/nginx" \
    "$destination/ros2_ws/src/patrolbot_web_bridge" "$destination/frontend/integration"
cp -r "$source_root/server/app" "$source_root/server/tests" "$source_root/server/maps" \
    "$source_root/server/pyproject.toml" "$source_root/server/constraints.txt" "$destination/server/"
cp -r "$source_root/shared" "$source_root/contracts" "$destination/"
cp "$source_root"/infrastructure/Dockerfile.* "$source_root"/infrastructure/*.yml \
    "$destination/infrastructure/"
cp "$source_root/infrastructure/nginx/nginx.conf" "$destination/infrastructure/nginx/"
cp -r "$source_root/ros2_ws/src/patrolbot_web_bridge/patrolbot_web_bridge" \
    "$destination/ros2_ws/src/patrolbot_web_bridge/"
cp "$source_root/frontend/integration/serve_mock.py" "$destination/frontend/integration/"
