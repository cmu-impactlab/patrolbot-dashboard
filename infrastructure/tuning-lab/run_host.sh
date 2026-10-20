#!/usr/bin/env bash
# Host-side wrapper for the isolated native tuning lab.
#
# Builds the offline lab image (COPY-only Dockerfile, no apt/pip/network), then
# runs it with `--network none` and exact image IDs bound into the receipt.
# Evidence lands in $EVIDENCE_DIR (receipt.json, native.log, generated YAML).
#
#   BASE_REF=patrolbot:joystick-handshake-diagnostic-20261015-r3 \
#   EVIDENCE_DIR=/tmp/opencode/tuning-evidence \
#   bash infrastructure/tuning-lab/run_host.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_REF="${BASE_REF:-patrolbot:joystick-handshake-diagnostic-20261015-r3}"
EVIDENCE_DIR="${EVIDENCE_DIR:-/tmp/opencode/tuning-evidence}"

# Resolve the locally cached base to its exact content ID. The receipt records
# this digest (never the mutable tag), so fail closed if it is not a full ID.
BASE_ID=$(docker --context default image inspect --format '{{.Id}}' "$BASE_REF")
if [[ ! "$BASE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "run_host: base '$BASE_REF' did not resolve to an exact sha256 image ID (got '${BASE_ID:-<none>}')" >&2
  exit 1
fi

# BuildKit's `FROM` cannot resolve a bare `sha256:<id>`: it parses as the Docker
# Hub repository `sha256`, then fails to pull it. Tag the cached base by content
# instead and build from that local tag with --pull=false + --network none, so
# the build stays offline on the exact cached bytes. Provenance is unchanged: the
# receipt still carries the exact BASE_ID (and BASE_REF) bound below.
BASE_TAG="patrolbot-tuning-base:${BASE_ID#sha256:}"
docker --context default image tag "$BASE_ID" "$BASE_TAG"
if ! TAGGED_ID=$(docker --context default image inspect --format '{{.Id}}' "$BASE_TAG"); then
  echo "run_host: could not inspect local base tag '$BASE_TAG'" >&2
  exit 1
fi
if [ "$TAGGED_ID" != "$BASE_ID" ]; then
  echo "run_host: local base tag '$BASE_TAG' resolves to '$TAGGED_ID', expected '$BASE_ID'" >&2
  exit 1
fi

docker --context default build --network none --pull=false \
  --build-arg PATROLBOT_NAV2_IMAGE="$BASE_TAG" \
  -f "$REPO_ROOT/infrastructure/tuning-lab/Dockerfile" \
  -t patrolbot-dashboard:tuning-lab "$REPO_ROOT"

LAB_ID=$(docker --context default image inspect --format '{{.Id}}' patrolbot-dashboard:tuning-lab)
echo "TUNING_LAB_IMAGE_ID=$LAB_ID"
echo "TUNING_LAB_BASE_IMAGE_ID=$BASE_ID"

mkdir -p "$EVIDENCE_DIR"
docker --context default run --rm --network none \
  --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
  -e HOME=/tmp -e ROS_HOME=/tmp/ros-home \
  -e TUNING_LAB_IMAGE_ID="$LAB_ID" \
  -e TUNING_LAB_IMAGE_REF=patrolbot-dashboard:tuning-lab \
  -e TUNING_LAB_BASE_IMAGE_ID="$BASE_ID" \
  -e TUNING_LAB_BASE_IMAGE_REF="$BASE_REF" \
  -v "$EVIDENCE_DIR":/tmp/tuning-lab-evidence \
  "$LAB_ID"
