#!/usr/bin/env bash
# Isolated software lab runner: no hardware, no external network, no SBC.
# Starts the lab, waits for its HTTP surface, then runs the receipt checker.
set -e
source /opt/ros/jazzy/setup.bash
source /opt/patrolbot/install/setup.bash
source /opt/web-teleop/install/setup.bash
exec python3 -u "$(dirname "${BASH_SOURCE[0]}")/teleop_lab.py" --supervise
