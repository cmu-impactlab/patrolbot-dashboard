#!/usr/bin/env bash
# Isolated native tuning lab runner: no hardware, no external network, no SBC.
# Every native ROS node runs only inside this --network none container with
# ROS_DOMAIN_ID 188 and loopback-only DDS. The lab itself refuses to start ROS
# unless the sandbox is isolated (only lo, domain >= 188, localhost-only).
#
# The host wrapper supplies exact image IDs via -e (tags alone are not evidence):
#   TUNING_LAB_IMAGE_ID / TUNING_LAB_IMAGE_REF
#   TUNING_LAB_BASE_IMAGE_ID / TUNING_LAB_BASE_IMAGE_REF
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-188}"
export ROS_LOCALHOST_ONLY=1
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export PATROLBOT_ISOLATED_TEST=1
export PYTHONDONTWRITEBYTECODE=1

source /opt/ros/jazzy/setup.bash
source /opt/patrolbot_ws/install/setup.bash
source /opt/patrolbot_nav2/install/setup.bash

EVIDENCE_DIR="${EVIDENCE_DIR:-/tmp/tuning-lab-evidence}"
mkdir -p "$EVIDENCE_DIR"

status=0
python3 /opt/dashboard/scripts/tuning_lab.py --output "$EVIDENCE_DIR/receipt.json" || status=$?

echo "--- receipt check ---"
if [ "$status" -eq 0 ]; then
  python3 /opt/dashboard/scripts/check_tuning_receipt.py "$EVIDENCE_DIR/receipt.json" || status=$?
fi

if [ "$status" -ne 0 ]; then
  echo "--- native log tail ---"
  tail -40 "$EVIDENCE_DIR/native.log" 2>/dev/null || true
fi
exit "$status"
