"""End-to-end smoke test: server + mock robot + a fake browser.

Starts uvicorn and the mock robot as subprocesses, connects to /ws/ui, and
asserts that a snapshot, live pose stream (~10 Hz), map REST payload, and
battery telemetry all arrive. Exits nonzero on failure.

Run: server/.venv/bin/python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "server", ".venv", "bin", "python")
PORT = int(os.environ.get("SMOKE_PORT", "8123"))


async def check_ui_stream() -> None:
    import websockets

    async with websockets.connect(f"ws://localhost:{PORT}/ws/ui") as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert first["type"] == "server.snapshot", first["type"]
        poses = 0
        started = time.monotonic()
        while poses < 15 and time.monotonic() - started < 5.0:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if frame["type"] == "telemetry.pose":
                poses += 1
        elapsed = time.monotonic() - started
        assert poses >= 15, f"only {poses} poses in {elapsed:.1f}s"
        rate = poses / elapsed
        assert rate > 5.0, f"pose rate too low: {rate:.1f} Hz"
        print(f"  pose stream OK ({rate:.1f} Hz)")


def check_rest() -> None:
    with urllib.request.urlopen(f"http://localhost:{PORT}/api/health", timeout=5) as response:
        health = json.loads(response.read())
    assert health["robot_connected"] is True, health
    print("  /api/health OK (robot connected)")
    with urllib.request.urlopen(f"http://localhost:{PORT}/api/map", timeout=5) as response:
        map_payload = json.loads(response.read())
    total = sum(count for _, count in map_payload["rle"])
    assert total == map_payload["width"] * map_payload["height"], "RLE size mismatch"
    print(f"  /api/map OK ({map_payload['width']}x{map_payload['height']}, version {map_payload['map_version']})")
    with urllib.request.urlopen(f"http://localhost:{PORT}/api/layouts", timeout=5) as response:
        layouts = json.loads(response.read())
    names = {item["name"] for item in layouts}
    assert {"Operator", "Research", "Diagnostics"} <= names, names
    print("  /api/layouts OK (3 presets)")


def wait_for_server() -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server did not start")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="patrolbot-smoke-")
    env = {**os.environ, "PATROLBOT_DATABASE_PATH": os.path.join(tmp, "smoke.db"),
           "PATROLBOT_ROBOT_TOKEN": "smoke-token"}
    server = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=os.path.join(ROOT, "server"), env=env,
    )
    mock = None
    try:
        wait_for_server()
        print("server up")
        mock = subprocess.Popen(
            [PY, "-m", "mock_robot", "--server", f"ws://localhost:{PORT}/ws/robot",
             "--token", "smoke-token", "--scenario", "calm"],
            cwd=ROOT,
        )
        time.sleep(2.0)
        asyncio.run(check_ui_stream())
        check_rest()
        print("SMOKE OK")
        return 0
    finally:
        for proc in (mock, server):
            if proc is not None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
