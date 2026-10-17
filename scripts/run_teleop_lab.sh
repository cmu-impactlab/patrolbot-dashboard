#!/usr/bin/env bash
# Isolated software lab runner: no hardware, no external network, no SBC.
# Starts the lab, waits for its HTTP surface, then runs the receipt checker.
set -e
source /opt/ros/jazzy/setup.bash
source /opt/patrolbot/install/setup.bash
source /opt/web-teleop/install/setup.bash
python3 /opt/dashboard/scripts/teleop_lab.py > /tmp/teleop_lab.out 2>&1 &
lab=$!
stop() { kill -TERM "$lab" 2>/dev/null; wait "$lab" 2>/dev/null; }
trap stop EXIT
python3 - <<'PY'
import sys, time, urllib.request
deadline = time.time() + 30
ok = False
while time.time() < deadline and not ok:
    try:
        urllib.request.urlopen("http://127.0.0.1:8027/lab/status", timeout=1)
        ok = True
    except Exception:
        time.sleep(0.3)
if not ok:
    print("lab HTTP never started")
    sys.exit(1)
PY
status=0
python3 /opt/dashboard/scripts/check_teleop_receipt.py || status=$?
echo "--- lab diagnostics ---" >&2
grep -h '"debug"' /tmp/teleop_lab.out | tail -40 >&2 || true
if [ "$status" -ne 0 ]; then
  echo "--- lab log tail ---"
  tail -80 /tmp/teleop_lab.out
fi
exit "$status"
