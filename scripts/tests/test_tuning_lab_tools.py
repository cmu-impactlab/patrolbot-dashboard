"""Unit tests for the isolated tuning-lab tools. No ROS, no network, no Docker.

They exercise the pure logic that can run anywhere: the isolation guard (which
must fail before any ROS import), the receipt validator's rejection of stale or
internally inconsistent "shim" receipts, and the CLI exit codes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import check_tuning_receipt as checker  # noqa: E402
import tuning_lab as lab  # noqa: E402


def _net_root(tmp_path: Path, *names: str) -> Path:
    root = tmp_path / "net"
    root.mkdir()
    for name in names:
        (root / name).touch()
    return root


def test_module_import_does_not_import_ros():
    # The lab defers every ROS import until after the isolation guard runs.
    assert "rclpy" not in sys.modules


def test_isolation_failures_accepts_only_loopback():
    good = {
        "patrolbot_isolated_test": "1",
        "ros_domain_id": "188",
        "ros_localhost_only": "1",
        "interfaces": ["lo"],
    }
    assert lab.isolation_failures(good) == []

    failures = lab.isolation_failures({**good, "interfaces": ["lo", "eth0"]})
    assert any("exactly ['lo']" in item for item in failures)


@pytest.mark.parametrize("field,value", [
    ("patrolbot_isolated_test", None),
    ("ros_domain_id", "7"),
    ("ros_domain_id", "not-an-int"),
    ("ros_localhost_only", "0"),
    ("interfaces", []),
])
def test_isolation_failures_rejects_each_violation(field, value):
    good = {
        "patrolbot_isolated_test": "1",
        "ros_domain_id": "188",
        "ros_localhost_only": "1",
        "interfaces": ["lo"],
    }
    assert lab.isolation_failures({**good, field: value})


def test_assert_isolated_raises_before_any_ros_import(tmp_path):
    root = _net_root(tmp_path, "lo", "enp7s0")
    snapshot = {
        "patrolbot_isolated_test": "1",
        "ros_domain_id": "188",
        "ros_localhost_only": "1",
        "interfaces": lab._interfaces(str(root)),
    }
    with pytest.raises(RuntimeError, match="refusing native ROS outside isolation"):
        lab.assert_isolated(snapshot)
    # The guard fired without importing ROS.
    assert "rclpy" not in sys.modules


def test_isolation_snapshot_reads_interfaces_from_link_root(tmp_path):
    root = _net_root(tmp_path, "lo")
    snapshot = lab.isolation_snapshot(str(root))
    assert snapshot["interfaces"] == ["lo"]


def test_receipt_self_test_rejects_every_shim():
    assert checker.self_test() == []


def test_valid_fixture_passes_and_records_no_physical_acceptance():
    fixture = checker.build_valid_fixture()
    assert checker.validate_receipt(fixture) == []
    assert fixture["physical_acceptance"] is False


def test_physical_acceptance_true_is_rejected():
    fixture = checker.build_valid_fixture()
    fixture["physical_acceptance"] = True
    assert checker.validate_receipt(fixture)


def test_stale_receipt_is_rejected():
    fixture = checker.build_valid_fixture()
    fixture["generated_at"] = "2000-01-01T00:00:00Z"
    assert checker.validate_receipt(fixture, max_age_seconds=3600)


def test_missing_image_id_is_rejected():
    fixture = checker.build_valid_fixture()
    fixture["images"]["lab_image_id"] = None
    assert checker.validate_receipt(fixture)


def test_amcl_live_claim_is_rejected():
    fixture = checker.build_valid_fixture()
    for check in fixture["checks"]:
        if check["id"] == "amcl_startup_readback":
            check["classification"] = "live_verified"
    errors = checker.validate_receipt(fixture)
    assert any("amcl_startup_readback" in item for item in errors)


def test_hot_reload_without_effect_is_rejected():
    fixture = checker.build_valid_fixture()
    for check in fixture["checks"]:
        if check["id"] == "vs_hot_reload_max_velocity_effect":
            check["detail"]["effect_observed"] = False
    assert checker.validate_receipt(fixture)


def test_isolation_gate_rejects_non_loopback_environment():
    with pytest.raises(RuntimeError, match="outside isolation"):
        checker.assert_isolated_environment({
            "patrolbot_isolated_test": "1",
            "ros_domain_id": "188",
            "ros_localhost_only": "1",
            "interfaces": ["lo", "eth0"],
        })


def test_checker_main_accepts_valid_and_rejects_corrupt(tmp_path):
    valid = tmp_path / "receipt.json"
    valid.write_text(json.dumps(checker.build_valid_fixture()))
    assert checker.main([str(valid), "--allow-unisolated"]) == 0

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert checker.main([str(corrupt), "--allow-unisolated"]) == 1

    oversized = tmp_path / "oversized.json"
    oversized.write_text(json.dumps(checker.build_valid_fixture()) + " " * (checker.MAX_RECEIPT_BYTES))
    assert checker.main([str(oversized), "--allow-unisolated"]) == 1


def test_checker_requires_isolation_by_default(tmp_path):
    valid = tmp_path / "receipt.json"
    valid.write_text(json.dumps(checker.build_valid_fixture()))
    # On a normal host with real interfaces the default isolation gate must fail.
    assert checker.main([str(valid)]) == 1
