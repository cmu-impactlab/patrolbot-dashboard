"""Exercise CI's dependency contract and disposable checkout input boundary."""
from pathlib import Path
import os
import shlex
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[2]
COPY = ROOT / "scripts/copy-constrained-test-sources.sh"
BRIDGE = Path("ros2_ws/src/patrolbot_web_bridge")


def test_bridge_ci_reuses_server_dev_contract():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["bridge"]
    assert set(job["strategy"]["matrix"]["python-version"]) == {"3.11", "3.12", "3.13", "3.14"}
    commands = [step for step in job["steps"] if "run" in step]
    assert shlex.split(commands[0]["run"]) == ["pip", "install", "./server[dev]"]
    assert shlex.split(commands[1]["run"]) == ["python", "-m", "pytest", "test", "-q"]
    assert commands[1]["working-directory"] == str(BRIDGE)
    assert commands[1]["env"]["PYTHONPATH"] == (
        "${{ github.workspace }}/server:${{ github.workspace }}/" + str(BRIDGE))
    project = tomllib.loads((ROOT / "server/pyproject.toml").read_text())["project"]
    dev = project["optional-dependencies"]["dev"]
    assert any(dep.startswith("pytest-asyncio>=") for dep in dev)
    assert any(dep.startswith("pytest-timeout>=") for dep in dev)
    assert any(dep.startswith("websockets>=") for dep in project["dependencies"])
    native = ET.parse(ROOT / BRIDGE / "package.xml").getroot()
    assert {"python3-pytest-asyncio", "python3-pytest-timeout"} <= {
        node.text for node in native.findall("test_depend")}
    assert not any("server" in (node.text or "") for node in native.findall("exec_depend"))


def test_constrained_copy_has_exact_inputs(tmp_path):
    # A synthetic checkout exposes accidental broad copies even on clean CI.
    source, destination = tmp_path / "source", tmp_path / "isolated"
    wanted = [
        "server/app/model.py", "server/tests/test_example.py", "server/maps/catalog/map.pgm",
        "server/pyproject.toml", "server/constraints.txt", "shared/schemas/fixture.json",
        "contracts/robot-tuning.registry.json", "infrastructure/nginx/nginx.conf",
        "infrastructure/Dockerfile.server", "infrastructure/docker-compose.local.yml",
        str(BRIDGE / "patrolbot_web_bridge/commands.py"),
        "frontend/integration/serve_mock.py",
    ]
    unwanted = [
        "server/.env", "server/.venv/bin/python", "server/local.egg-info/PKG-INFO",
        "frontend/node_modules/dependency/index.js", "frontend/dist/index.html",
        "frontend/public/maps/duplicate.pgm", "frontend/integration/.env",
        "frontend/integration/other.py", "infrastructure/nginx/certs/private.key",
        str(BRIDGE / ".env"), "ros2_ws/install/setup.bash", ".git/config",
    ]
    for name in wanted + unwanted:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    subprocess.run(["bash", str(COPY), str(source), str(destination)], check=True)
    copied = {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()}
    assert copied == set(wanted)
    for name in wanted:
        assert (destination / name).read_bytes() == (source / name).read_bytes()


def test_constraints_job_uses_tested_copy_helper():
    script = (ROOT / "scripts/check-constraints.sh").read_text()
    assert "bash /repo/scripts/copy-constrained-test-sources.sh /repo /tmp/repo" in script
    assert "PYTHONPATH=/tmp/repo/server:/tmp/repo/ros2_ws/src/patrolbot_web_bridge" in script


def test_real_checkout_copy_imports_bridge_without_checkout_fallback(tmp_path):
    destination = tmp_path / "isolated"
    subprocess.run(["bash", str(COPY), str(ROOT), str(destination)], check=True)
    # No site packages or editable installs: only the disposable source is visible.
    subprocess.run([sys.executable, "-S", "-c", """
from pathlib import Path
import patrolbot_web_bridge.commands as commands
root = Path.cwd()
assert Path(commands.__file__).is_relative_to(root)
assert (root / 'frontend/integration/serve_mock.py').is_file()
"""], cwd=destination, env={**os.environ, "PYTHONPATH": str(destination / BRIDGE)}, check=True)
