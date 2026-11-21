"""Historical release verification must not substitute current map packaging."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_release import repository_mismatches


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
