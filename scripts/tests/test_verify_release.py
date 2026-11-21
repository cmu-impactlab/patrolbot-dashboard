"""Historical release verification must not substitute current map packaging."""
import hashlib
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
    root = Path(__file__).resolve().parents[2]
    data = subprocess.check_output(["git", "show", "HEAD:README.md"], cwd=root)
    git_dir = subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"], cwd=root).decode().strip()
    monkeypatch.setenv("GIT_DIR", git_dir)
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    (tmp_path / "README.md").write_bytes(b"different working tree")
    entry = {"repository_path": "README.md", "sha256": hashlib.sha256(data).hexdigest()}
    assert repository_mismatches(tmp_path, [entry])
    assert repository_mismatches(tmp_path, [entry], "HEAD") == []
