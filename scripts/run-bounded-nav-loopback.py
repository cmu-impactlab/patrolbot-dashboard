#!/usr/bin/env python3
"""Run the real picker/server against a loopback-only simulated Nav2 peer."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener, ProxyHandler


ROOT = Path(__file__).resolve().parents[1]


def main():
    # Never inherit a production URL, database, token, or OIDC configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PATROLBOT_", "VITE_"))}
    token = secrets.token_urlsafe(32)
    playwright = ROOT / "frontend/node_modules/.bin/playwright"
    if not playwright.is_file():
        raise SystemExit("Install the dashboard frontend dependencies first.")
    with tempfile.TemporaryDirectory(prefix="patrolbot-nav-loopback-") as directory:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            backend = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app",
                 "--fd", str(listener.fileno()), "--no-access-log", "--log-level", "warning"],
                cwd=directory, pass_fds=(listener.fileno(),),
                env={**env, "PYTHONPATH": str(ROOT / "server"),
                     "PATROLBOT_ROBOT_TOKEN": token,
                     "PATROLBOT_AUTH_MODE": "local",
                     "PATROLBOT_DEFAULT_ROBOT_ID": "bounded-nav2-loopback",
                     "PATROLBOT_DATABASE_PATH": str(Path(directory) / "loopback.db")},
            )
        try:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and backend.poll() is None:
                try:
                    with opener.open(f"{url}/api/health", timeout=.25) as health:
                        data = json.load(health)
                    if data["status"] == "healthy" and data["robot_connected"] is False:
                        break
                except OSError:
                    pass
                time.sleep(.1)
            else:
                raise SystemExit("The fresh loopback server did not become healthy.")
            return subprocess.run(
                [str(playwright), "test", "autonomous-speed-loopback.spec.ts", "--project", "desktop-chromium"],
                cwd=ROOT / "frontend",
                env={**env, "PATROLBOT_DEV_BACKEND": url, "PATROLBOT_NAV_LOOPBACK_TOKEN": token},
                timeout=120, check=False,
            ).returncode
        finally:
            if backend.poll() is None:
                backend.terminate()
                try:
                    backend.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    backend.kill()
                    backend.wait()


if __name__ == "__main__":
    sys.exit(main())
