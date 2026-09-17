"""Isolated localhost backend + mock robot, with a disposable database.

Never uses deployment configuration or connects to a physical robot.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "server/.venv/bin/python"
PORT = 8127
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--host", choices=["127.0.0.1", "0.0.0.0"], default="127.0.0.1",
                    help="Use 0.0.0.0 for user-run tests from devices on your LAN.")
args = parser.parse_args()
processes = []


def shutdown(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)
with tempfile.TemporaryDirectory(prefix="patrolbot-mobile-") as directory:
    env = {**os.environ, "PATROLBOT_DATABASE_PATH": f"{directory}/test.db",
           "PATROLBOT_DATABASE_URL": "", "PATROLBOT_ROBOT_TOKEN": "mobile-test-only",
           "PATROLBOT_AUTH_MODE": "local", "PATROLBOT_ENVIRONMENT": "development",
           "PATROLBOT_DEFAULT_ROBOT_ID": "patrolbot-01",
           "PATROLBOT_STATIC_MAP_YAML": "", "PATROLBOT_ALLOWED_ORIGINS": ""}
    try:
        processes.append(subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", args.host,
             "--port", str(PORT), "--log-level", "warning"], cwd=ROOT / "server", env=env))
        for attempt in range(50):
            if processes[0].poll() is not None:
                raise RuntimeError("Isolated backend failed to start")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Isolated backend did not become ready")
        processes.append(subprocess.Popen(
            [str(PYTHON), "-m", "mock_robot", "--server", f"ws://127.0.0.1:{PORT}/ws/robot",
             "--token", "mobile-test-only", "--scenario", "calm"], cwd=ROOT, env=env))
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
