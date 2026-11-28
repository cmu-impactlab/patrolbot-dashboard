"""Historical release verification must not substitute current map packaging."""
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_release import main, remote_mismatches, repository_mismatches


def test_empty_inventory_fails_offline(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "synthetic.json"
    manifest.write_text(json.dumps({"files": []}))
    monkeypatch.setattr(sys, "argv", ["verify_release.py", str(manifest)])
    assert main() is True
    captured = capsys.readouterr()
    assert "empty release file inventory" in captured.err
    assert json.loads(captured.out)["passed"] is False
    assert repository_mismatches(tmp_path, [], "release")


def test_remote_hosts_cover_all_artifact_kinds():
    manifest = {
        "files": [{"host": "files-only"}, {"host": "shared"}],
        "containers": [{"host": "containers-only"}, {"host": "shared"}],
        "executables": [{"host": "executables-only"}, {"host": "shared"}],
        "docker_commands": {"containers-only": ["sudo", "docker"]},
    }
    with patch("verify_release.subprocess.run", return_value=subprocess.CompletedProcess(
            [], 0, '[]', '')) as run:
        assert remote_mismatches(manifest) == []
    assert [call.args[0][-2] for call in run.call_args_list] == [
        "containers-only", "executables-only", "files-only", "shared"]
    for call in run.call_args_list:
        host = call.args[0][-2]
        payload = json.loads(call.kwargs["input"])
        for key in ("files", "containers", "executables"):
            assert payload[key] == [e for e in manifest[key] if e["host"] == host]
        assert payload["docker_command"] == manifest["docker_commands"].get(host, ["docker"])


@pytest.mark.parametrize("matches", [True, False])
def test_remote_hash_program_with_pure_fakes(matches):
    data = b"synthetic release bytes"
    digest = hashlib.sha256(data if matches else b"different").hexdigest()
    manifest = {
        "files": [{"host": "fake", "remote_path": "/file", "sha256": digest},
                  {"host": "fake", "container": "app", "remote_path": "/app/file", "sha256": digest}],
        "containers": [{"host": "fake", "name": "app", "image": "expected"}],
        "executables": [{"host": "fake", "name": "worker", "pid_command": ["fake-pid"], "sha256": digest}],
    }

    def fake_run(command, *, input, **kwargs):
        program = shlex.split(command[-1])[-1]
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(input)), redirect_stdout(output), \
                patch("builtins.open", side_effect=lambda *a, **k: io.BytesIO(data)), \
                patch("subprocess.check_output", side_effect=[
                    json.dumps([{"Image": "expected" if matches else "different"}]).encode(),
                    data, b"42"]):
            exec(compile(program, "<fake-remote>", "exec"), {})
        return subprocess.CompletedProcess(command, 0, output.getvalue(), "")

    with patch("verify_release.subprocess.run", side_effect=fake_run):
        failures = remote_mismatches(manifest)
    assert failures == ([] if matches else [
        "image mismatch: app", "remote mismatch: /file", "remote mismatch: /app/file",
        "running executable mismatch: worker"])


def test_remote_inspection_failure_is_not_success(capsys):
    with patch("verify_release.subprocess.run", return_value=subprocess.CompletedProcess(
            [], 1, '', 'synthetic inspection error')):
        assert remote_mismatches({"containers": [{"host": "fake"}]}) == [
            "read-only inspection failed: fake"]
    assert "synthetic inspection error" in capsys.readouterr().err


def test_current_packaging_does_not_satisfy_removed_historical_path(tmp_path):
    data = b"original map"
    canonical = tmp_path / "server/maps/catalog/second_map.pgm"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(data)
    entry = {"repository_path": "server/maps/cmuq-floor2/second_map.pgm",
             "sha256": hashlib.sha256(data).hexdigest()}
    assert repository_mismatches(tmp_path, [entry]) == [
        "repository mismatch: " + entry["repository_path"]]
    with patch("verify_release.subprocess.check_output", return_value=data) as show:
        assert repository_mismatches(tmp_path, [entry], "old-release") == []
    show.assert_called_once_with(
        ["git", "show", "old-release:" + entry["repository_path"]], cwd=tmp_path)
    with patch("verify_release.subprocess.check_output", return_value=b"changed map"):
        assert repository_mismatches(tmp_path, [entry], "old-release")
    with patch("verify_release.subprocess.check_output",
               side_effect=subprocess.CalledProcessError(128, "git")):
        assert repository_mismatches(tmp_path, [entry], "missing-release")


def test_current_file_requires_exact_hash(tmp_path):
    path = tmp_path / "map.pgm"
    path.write_bytes(b"map")
    entry = {"repository_path": "map.pgm", "sha256": hashlib.sha256(b"map").hexdigest()}
    assert repository_mismatches(tmp_path, [entry]) == []
    path.write_bytes(b"changed")
    assert repository_mismatches(tmp_path, [entry])


def test_real_git_source_ref_ignores_worktree(tmp_path, monkeypatch):
    # Isolate real Git from both the checkout's metadata and inherited overrides.
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    subprocess.run(["git", "init", "--template=", str(tmp_path)], check=True, capture_output=True)
    data = b"synthetic committed map\n"
    path = tmp_path / "README.md"
    path.write_bytes(data)
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Release Test", "-c", "user.email=release@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic release"],
                   cwd=tmp_path, check=True, capture_output=True)
    ref = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path).decode().strip()
    path.write_bytes(b"different working tree")
    entry = {"repository_path": "README.md", "sha256": hashlib.sha256(data).hexdigest()}
    assert repository_mismatches(tmp_path, [entry])
    assert repository_mismatches(tmp_path, [entry], ref) == []
    mutated = {**entry, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert repository_mismatches(tmp_path, [mutated], ref)
    missing = {**entry, "repository_path": "missing.pgm"}
    assert repository_mismatches(tmp_path, [missing], ref)
    assert repository_mismatches(tmp_path, [entry], "missing-release")
    path.unlink()
    assert repository_mismatches(tmp_path, [entry])
    assert repository_mismatches(tmp_path, [entry], ref) == []
