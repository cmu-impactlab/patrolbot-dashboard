"""Unit tests for the robot-tuning contract parity tool.

Pure stdlib + pytest: no core checkout, no network, no ROS. These run in the
portable CI job. They exercise the tool's own logic (hashing, comparison, exit
codes) against temporary fixtures; the vendored canonical contract's own tests
live in ``contracts/test_robot_tuning.py`` and are run separately.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# The tool lives one directory up in scripts/; import it as a plain module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_tuning_contract as parity  # noqa: E402


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_core(tmp_path: Path) -> Path:
    """A canonical repo whose six artifacts all exist and are unique."""
    core = tmp_path / "patrolbot"
    for index, name in enumerate(parity.CONTRACT_FILES):
        _write(core / parity.CONTRACT_SUBDIR / name, f"canonical {index} {name}\n".encode())
    return core


def _vendor_all(core: Path, vendored: Path) -> None:
    vendored.mkdir(parents=True, exist_ok=True)
    for name in parity.CONTRACT_FILES:
        (vendored / name).write_bytes((core / parity.CONTRACT_SUBDIR / name).read_bytes())


def test_sha256_file_matches_hashlib(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01binary\xff")
    assert parity.sha256_file(target) == hashlib.sha256(b"\x00\x01binary\xff").hexdigest()


def test_all_vendored_matches_cleanly(tmp_path):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)

    results = parity.compare_contracts(core, vendored)

    assert [r.status for r in results] == [parity.MATCH] * len(parity.CONTRACT_FILES)
    assert parity.exit_code(results) == 0
    assert all(r.core_sha256 == r.vendored_sha256 for r in results)


def test_single_byte_drift_is_a_mismatch(tmp_path):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)
    drifted = vendored / "robot-tuning.registry.json"
    drifted.write_bytes(drifted.read_bytes().replace(b"canonical", b"tampered!"))

    results = parity.compare_contracts(core, vendored)
    by_name = {r.name: r for r in results}

    assert by_name["robot-tuning.registry.json"].status == parity.MISMATCH
    assert parity.exit_code(results) == 1


def test_empty_vendored_dir_fails_closed(tmp_path):
    """The pre-vendor state is an error: parity never passes on nothing."""
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    vendored.mkdir()

    results = parity.compare_contracts(core, vendored)

    assert all(r.status == parity.MISSING_VENDORED for r in results)
    assert parity.exit_code(results) == 1


def test_missing_vendored_copy_is_fatal(tmp_path):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)
    (vendored / "validate_robot_tuning.py").unlink()

    results = parity.compare_contracts(core, vendored)
    missing = [r for r in results if r.status == parity.MISSING_VENDORED]

    assert [r.name for r in missing] == ["validate_robot_tuning.py"]
    assert parity.exit_code(results) == 1


def test_unreadable_vendored_copy_is_exit_1(tmp_path, monkeypatch):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)
    target = vendored / "robot-tuning.schema.json"
    real = parity.sha256_file

    def fake(path):
        if Path(path) == target:
            raise PermissionError("simulated unreadable vendored copy")
        return real(path)

    monkeypatch.setattr(parity, "sha256_file", fake)

    results = parity.compare_contracts(core, vendored)
    by_name = {r.name: r for r in results}

    assert by_name["robot-tuning.schema.json"].status == parity.UNREADABLE_VENDORED
    assert "cannot read vendored copy" in by_name["robot-tuning.schema.json"].detail
    assert parity.exit_code(results) == 1


def test_missing_core_artifact_is_a_canonical_error(tmp_path):
    core = _make_core(tmp_path)
    (core / parity.CONTRACT_SUBDIR / "robot-tuning.fixtures.json").unlink()
    vendored = tmp_path / "contracts"
    vendored.mkdir()

    results = parity.compare_contracts(core, vendored)

    assert parity.exit_code(results) == 2
    assert any(r.status == parity.MISSING_CORE for r in results)


def test_unreadable_core_artifact_is_exit_2(tmp_path, monkeypatch):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)
    target = core / parity.CONTRACT_SUBDIR / "robot-tuning.registry.json"
    real = parity.sha256_file

    def fake(path):
        if Path(path) == target:
            raise PermissionError("simulated unreadable canonical artifact")
        return real(path)

    monkeypatch.setattr(parity, "sha256_file", fake)

    results = parity.compare_contracts(core, vendored)
    by_name = {r.name: r for r in results}

    assert by_name["robot-tuning.registry.json"].status == parity.UNREADABLE_CORE
    assert "cannot read canonical artifact" in by_name["robot-tuning.registry.json"].detail
    assert parity.exit_code(results) == 2


def test_main_reports_and_writes_json(tmp_path, capsys):
    core = _make_core(tmp_path)
    vendored = tmp_path / "contracts"
    _vendor_all(core, vendored)
    report = tmp_path / "report.json"

    code = parity.main(["--core-repo", str(core), "--vendored-dir", str(vendored),
                        "--report-json", str(report)])

    assert code == 0
    payload = json.loads(report.read_text())
    assert payload["exit_code"] == 0
    assert "allow_missing_vendored" not in payload
    assert {entry["status"] for entry in payload["files"]} == {parity.MATCH}
    assert "summary:" in capsys.readouterr().out


def test_main_missing_core_repo_is_exit_2(tmp_path):
    code = parity.main(["--core-repo", str(tmp_path / "does-not-exist")])
    assert code == 2


def test_core_repo_argument_is_required():
    with pytest.raises(SystemExit) as excinfo:
        parity.main([])
    assert excinfo.value.code == 2


def test_allow_missing_vendored_flag_is_gone():
    """The bypass flag was removed; an empty vendor dir must never pass."""
    parser = parity.build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--core-repo", "/tmp", "--allow-missing-vendored"])
    assert excinfo.value.code == 2
