"""Validate an isolated native tuning receipt. Pure stdlib, no ROS, no network.

The checker is deliberately strict and self-consistent: a receipt only passes if
its own evidence agrees with its own conclusions. A fabricated, stale or
internally inconsistent receipt (a "shim") fails validation rather than being
trusted.

``--self-test`` runs the built-in negative fixtures so the checker's own
rejection logic is exercised without any ROS or network access.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

RECEIPT_SCHEMA_VERSION = 1
MAX_RECEIPT_BYTES = 1 << 20
MAX_CHECKS = 64
MAX_LIST = 4096
MIN_DOMAIN_ID = 188
HEX64 = set("0123456789abcdef")

REQUIRED_CHECKS = {
    "isolation_enforced",
    "native_inventory",
    "vs_startup_readback",
    "vs_caps_reduced_and_symmetric",
    "vs_over_cap_input_clamped",
    "vs_proportional_small_target_no_creep",
    "vs_reversal_through_zero",
    "vs_acceleration_bounded",
    "vs_stop_explicit_zero_deadline",
    "vs_stop_silence_timeout_deadline",
    "vs_hot_reload_max_velocity_effect",
    "amcl_startup_readback",
    "rpp_startup_readback",
}
ALLOWED_CLASSIFICATIONS = {"inventory", "live_verified", "restart_required", "unverified"}
RESTART_ONLY_CHECKS = {"amcl_startup_readback", "rpp_startup_readback"}
LINEAR_CAP = 0.30
ANGULAR_CAP = 0.70
BOUND_EPSILON = 1e-3


def _is_hex64(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def _bounded(value, limit: int) -> bool:
    return isinstance(value, list) and len(value) <= limit


def _checks_by_id(receipt: dict) -> dict:
    return {check.get("id"): check for check in receipt.get("checks", [])
            if isinstance(check, dict)}


def validate_receipt(receipt: dict, max_age_seconds: float | None = None) -> list[str]:
    errors: list[str] = []

    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object"]
    def finite_tree(value, depth=0):
        if depth > 32:
            return False
        if isinstance(value, float):
            return math.isfinite(value)
        if isinstance(value, dict):
            return all(finite_tree(item, depth + 1) for item in value.values())
        if isinstance(value, list):
            return len(value) <= MAX_LIST and all(finite_tree(item, depth + 1) for item in value)
        return True
    if not finite_tree(receipt):
        return ["receipt contains non-finite or unbounded data"]
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RECEIPT_SCHEMA_VERSION}")
    if receipt.get("generator") != "patrolbot-tuning-native-lab":
        errors.append("unexpected generator")
    if receipt.get("physical_acceptance") is not False:
        errors.append("physical_acceptance must be false")
    if receipt.get("live_pi_evidence") is not False:
        errors.append("live_pi_evidence must be false (image-internal evidence only)")
    if receipt.get("synthetic_inputs_only") is not True:
        errors.append("synthetic_inputs_only must be true")

    generated_at = receipt.get("generated_at")
    try:
        stamp = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        errors.append("generated_at must be an RFC3339 UTC 'Z' timestamp")
        stamp = None
    if stamp is not None and max_age_seconds is not None:
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if age > max_age_seconds:
            errors.append(f"receipt is stale: age {int(age)}s exceeds {int(max_age_seconds)}s")

    images = receipt.get("images") or {}
    if not isinstance(images.get("lab_image_id"), str) or not images["lab_image_id"].startswith("sha256:"):
        errors.append("images.lab_image_id must be an exact sha256 image ID")
    if not isinstance(images.get("base_image_id"), str) or not images["base_image_id"].startswith("sha256:"):
        errors.append("images.base_image_id must be an exact sha256 image ID")

    isolation = receipt.get("isolation") or {}
    if isolation.get("patrolbot_isolated_test") != "1":
        errors.append("isolation.patrolbot_isolated_test must be 1")
    try:
        if int(isolation.get("ros_domain_id")) < MIN_DOMAIN_ID:
            errors.append(f"isolation.ros_domain_id must be >= {MIN_DOMAIN_ID}")
    except (TypeError, ValueError):
        errors.append("isolation.ros_domain_id must be an integer")
    if list(isolation.get("interfaces", [])) != ["lo"]:
        errors.append("isolation.interfaces must be exactly ['lo'] (network-none / no host net)")
    if not str(isolation.get("ros_localhost_only", "")).lower() in {"1", "true"}:
        errors.append("isolation.ros_localhost_only must be enabled")
    if isolation.get("network_none_only_loopback") is not True:
        errors.append("isolation.network_none_only_loopback must be true")

    inventory = receipt.get("inventory") or {}
    provenance = inventory.get("provenance") or {}
    if not _is_hex64(provenance.get("nav2_upstream_archive_sha256")):
        errors.append("provenance.nav2_upstream_archive_sha256 must be a 64-hex digest")
    if not _is_hex64(provenance.get("smoother_params_sha256")):
        errors.append("provenance.smoother_params_sha256 must be a 64-hex digest")
    if not _is_hex64(provenance.get("nav2_params_sha256")):
        errors.append("provenance.nav2_params_sha256 must be a 64-hex digest")
    patches = provenance.get("patches")
    if not _bounded(patches, MAX_LIST) or not patches:
        errors.append("provenance.patches must be a non-empty bounded list")
    else:
        if provenance.get("patch_count") != len(patches):
            errors.append("provenance.patch_count must equal len(patches)")
        for patch in patches:
            if not _is_hex64(patch.get("sha256")):
                errors.append(f"patch {patch.get('name')} is missing a 64-hex sha256")
    libraries = inventory.get("libraries")
    if not _bounded(libraries, MAX_LIST) or not libraries:
        errors.append("inventory.libraries must be a non-empty bounded list")
    else:
        for library in libraries:
            if not library.get("path") or not _is_hex64(library.get("sha256")):
                errors.append(f"library {library.get('path')} is missing a 64-hex sha256")

    checks = receipt.get("checks")
    if not _bounded(checks, MAX_CHECKS) or not checks:
        errors.append("checks must be a non-empty bounded list")
        return errors
    by_id = _checks_by_id(receipt)
    missing = REQUIRED_CHECKS - set(by_id)
    if missing:
        errors.append("missing required checks: " + ", ".join(sorted(missing)))
    for check in checks:
        identifier = check.get("id")
        if check.get("status") not in {"pass", "fail"}:
            errors.append(f"check {identifier} has an invalid status")
        if check.get("classification") not in ALLOWED_CLASSIFICATIONS:
            errors.append(f"check {identifier} has an invalid classification")
        if not isinstance(check.get("detail"), dict):
            errors.append(f"check {identifier} detail must be an object")

    errors.extend(_validate_claims(by_id))

    summary = receipt.get("summary") or {}
    failed_count = sum(1 for check in checks if check.get("status") == "fail")
    if failed_count:
        errors.append(f"receipt contains {failed_count} failed native checks")
    if len(by_id) != len(checks):
        errors.append("duplicate native check identifiers")
    if summary.get("failed") != failed_count:
        errors.append("summary.failed does not match the checks")
    if summary.get("checks") != len(checks):
        errors.append("summary.checks does not match the checks")
    expected_result = "fail" if failed_count else "pass"
    if summary.get("result") != expected_result:
        errors.append("summary.result is inconsistent with the checks")
    return errors


def _validate_claims(by_id: dict) -> list[str]:
    errors: list[str] = []

    for identifier in RESTART_ONLY_CHECKS:
        check = by_id.get(identifier)
        if check and check.get("classification") != "restart_required":
            errors.append(f"{identifier} must be classified restart_required, not "
                          f"{check.get('classification')} (no offline hot-effect proof)")

    startup = by_id.get("vs_startup_readback")
    if startup and startup.get("status") == "pass":
        detail = startup.get("detail", {})
        expected = detail.get("expected", {})
        readback = detail.get("readback", {})
        if not expected or set(expected) - set(readback):
            errors.append("vs_startup_readback pass lacks matching readback evidence")
        for key, values in expected.items():
            observed = readback.get(key)
            if not isinstance(observed, list) or len(observed) != len(values):
                errors.append(f"vs_startup_readback {key} readback shape mismatch")
                continue
            if any(abs(a - b) > 1e-6 for a, b in zip(observed, values)):
                errors.append(f"vs_startup_readback {key} readback does not match the YAML")

    proportional = by_id.get("vs_proportional_small_target_no_creep")
    if proportional and proportional.get("status") == "pass":
        detail = proportional.get("detail", {})
        target = detail.get("target")
        cap = detail.get("cap")
        for key in ("early_max", "late_window_max"):
            value = detail.get(key)
            if value is None or target is None:
                continue
            if value > target + 0.02 + 1e-9:
                errors.append(f"proportional {key} suggests creep toward the cap")
            if cap is not None and value > cap + 1e-9:
                errors.append(f"proportional {key} exceeds the configured cap")

    caps = by_id.get("vs_caps_reduced_and_symmetric")
    if caps and caps.get("status") == "pass":
        detail = caps.get("detail", {})
        if abs(detail.get("configured_max_linear", 9) - LINEAR_CAP) > 1e-6:
            errors.append("vs_caps_reduced_and_symmetric must show the 0.30 m/s linear cap")
        if abs(detail.get("configured_max_angular", 9) - ANGULAR_CAP) > 1e-6:
            errors.append("vs_caps_reduced_and_symmetric must show the 0.70 rad/s angular cap")

    clamped = by_id.get("vs_over_cap_input_clamped")
    if clamped and clamped.get("status") == "pass":
        detail = clamped.get("detail", {})
        if not isinstance(detail.get("samples"), int) or detail["samples"] < 5:
            errors.append("clamping proof requires observed native output samples")
        if detail.get("peak_linear", 9) > LINEAR_CAP + BOUND_EPSILON:
            errors.append("vs_over_cap_input_clamped peak_linear exceeds the cap")
        if detail.get("peak_angular", 9) > ANGULAR_CAP + BOUND_EPSILON:
            errors.append("vs_over_cap_input_clamped peak_angular exceeds the cap")

    hot = by_id.get("vs_hot_reload_max_velocity_effect")
    if hot and hot.get("classification") == "live_verified":
        detail = hot.get("detail", {})
        if not isinstance(detail.get("samples"), int) or detail["samples"] < 5:
            errors.append("hot effect proof requires observed native output samples")
        if detail.get("set_accepted") is not True:
            errors.append("hot effect proof requires an accepted parameter update")
        if detail.get("effect_observed") is not True:
            errors.append("a live_verified hot-reload check requires effect_observed true")
        requested = detail.get("requested") or [None]
        peak = detail.get("peak_output")
        if peak is not None and requested[0] is not None and peak > requested[0] + BOUND_EPSILON:
            errors.append("live_verified hot-reload peak_output exceeds the requested clamp")
    return errors


def assert_isolated_environment(snapshot: dict | None = None) -> None:
    """Fail early when not in a --network none, loopback-only sandbox.

    This runs before any receipt trust and has no ROS dependency; the native lab
    applies the same guard before importing rclpy or creating a publisher.
    """
    if snapshot is None:
        root = Path(os.environ.get("TUNING_LAB_NET_PATH", "/sys/class/net"))
        try:
            interfaces = sorted(path.name for path in root.iterdir())
        except OSError:
            interfaces = []
        snapshot = {
            "patrolbot_isolated_test": os.environ.get("PATROLBOT_ISOLATED_TEST"),
            "ros_domain_id": os.environ.get("ROS_DOMAIN_ID"),
            "ros_localhost_only": os.environ.get("ROS_LOCALHOST_ONLY"),
            "interfaces": interfaces,
        }
    failures: list[str] = []
    if snapshot.get("patrolbot_isolated_test") != "1":
        failures.append("PATROLBOT_ISOLATED_TEST must be 1")
    try:
        if int(snapshot.get("ros_domain_id")) < MIN_DOMAIN_ID:
            failures.append(f"ROS_DOMAIN_ID must be >= {MIN_DOMAIN_ID}")
    except (TypeError, ValueError):
        failures.append("ROS_DOMAIN_ID must be an integer")
    if list(snapshot.get("interfaces", [])) != ["lo"]:
        failures.append("interfaces must be exactly ['lo'] (use --network none)")
    if not str(snapshot.get("ros_localhost_only", "")).lower() in {"1", "true"}:
        failures.append("ROS_LOCALHOST_ONLY must be enabled")
    if failures:
        raise RuntimeError("refusing to validate a native receipt outside isolation: "
                           + "; ".join(failures))


# ---------------------------------------------------------------------------
# Self-test fixtures: a valid receipt plus deliberate shims that must be rejected.
# ---------------------------------------------------------------------------


def _check(identifier: str, status: str = "pass", classification: str = "live_verified", detail=None):
    return {"id": identifier, "status": status, "classification": classification,
            "detail": detail if detail is not None else {}}


def build_valid_fixture() -> dict:
    smoother = {"max_velocity": [0.30, 0.0, 0.70], "min_velocity": [-0.30, 0.0, -0.70],
                "max_accel": [1.0, 0.0, 1.5], "max_decel": [-1.4, 0.0, -2.0]}
    checks = [
        _check("isolation_enforced", classification="inventory"),
        _check("native_inventory", classification="inventory"),
        _check("vs_upstream_defaults_observed", classification="inventory"),
        _check("vs_startup_readback", detail={"expected": smoother, "readback": smoother,
                                              "mismatches": {}}),
        _check("vs_caps_reduced_and_symmetric",
               detail={"configured_max_linear": 0.30, "configured_max_angular": 0.70,
                       "configured_min_linear": -0.30, "configured_max_accel": 1.0}),
        _check("vs_over_cap_input_clamped",
               detail={"peak_linear": 0.30, "peak_angular": 0.70, "samples": 20}),
        _check("vs_proportional_small_target_no_creep",
               detail={"target": 0.05, "final_output": 0.05, "cap": 0.30}),
        _check("vs_reversal_through_zero", detail={"crossed_zero": True}),
        _check("vs_acceleration_bounded", detail={"peak_dv_per_s": 1.0}),
        _check("vs_stop_explicit_zero_deadline", detail={"observed_s": 0.3}),
        _check("vs_stop_silence_timeout_deadline", detail={"observed_s": 0.65}),
        _check("vs_hot_reload_max_velocity_effect",
               detail={"effect_observed": True, "requested": [0.10, 0.0, 0.30],
                       "readback": [0.10, 0.0, 0.30], "peak_output": 0.10,
                       "set_accepted": True, "samples": 20}),
        _check("amcl_startup_readback", classification="restart_required",
               detail={"mutability_reason": "offline hot behaviour unproven"}),
        _check("rpp_startup_readback", classification="restart_required",
               detail={"mutability_reason": "offline hot behaviour unproven"}),
        _check("rpp_runtime_set_not_effect", classification="restart_required",
               detail={"set_accepted": True}),
    ]
    patches = [{"name": "0001.patch", "sha256": "a" * 64}]
    return {
        "schema_version": 1,
        "generator": "patrolbot-tuning-native-lab",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "physical_acceptance": False,
        "live_pi_evidence": False,
        "synthetic_inputs_only": True,
        "images": {"lab_image_id": "sha256:" + "1" * 64,
                   "base_image_id": "sha256:" + "2" * 64},
        "isolation": {"patrolbot_isolated_test": "1", "ros_domain_id": "188",
                      "ros_localhost_only": "1", "interfaces": ["lo"],
                      "network_none_only_loopback": True},
        "inventory": {
            "packages": {"nav2_amcl": {"apt_version": "1.3.12", "overlay_prefix": "/opt"}},
            "libraries": [{"path": "/opt/libamcl_core.so", "sha256": "b" * 64, "bytes": 1}],
            "provenance": {
                "nav2_upstream_archive_sha256": "c" * 64,
                "smoother_params_sha256": "d" * 64,
                "nav2_params_sha256": "e" * 64,
                "patch_count": len(patches),
                "patches": patches,
            },
        },
        "checks": checks,
        "summary": {"result": "pass", "checks": len(checks), "passed": len(checks),
                    "failed": 0, "classifications": {}},
    }


def self_test() -> list[str]:
    """Return a list of failures; empty means the checker rejects every shim."""
    failures: list[str] = []
    base = build_valid_fixture()
    if validate_receipt(base):
        failures.append("valid fixture was rejected: " + "; ".join(validate_receipt(base)))

    shims = {
        "physical_acceptance_true": lambda r: r.update(physical_acceptance=True),
        "live_pi_evidence_true": lambda r: r.update(live_pi_evidence=True),
        "missing_image_id": lambda r: r["images"].update(lab_image_id=None),
        "non_loopback_interfaces": lambda r: r["isolation"].update(interfaces=["lo", "eth0"]),
        "low_domain_id": lambda r: r["isolation"].update(ros_domain_id="7"),
        "localhost_only_off": lambda r: r["isolation"].update(ros_localhost_only="0"),
        "missing_patch_hash": lambda r: r["inventory"]["provenance"]["patches"][0].update(sha256=None),
        "empty_libraries": lambda r: r["inventory"].update(libraries=[]),
        "missing_required_check": lambda r: r.__setitem__(
            "checks", [c for c in r["checks"] if c["id"] != "vs_reversal_through_zero"]),
        "amcl_live_claim": lambda r: next(
            c for c in r["checks"] if c["id"] == "amcl_startup_readback").update(
                classification="live_verified"),
        "hot_reload_without_effect": lambda r: next(
            c for c in r["checks"] if c["id"] == "vs_hot_reload_max_velocity_effect")["detail"].update(
                effect_observed=False),
        "hot_reload_peak_over_request": lambda r: next(
            c for c in r["checks"] if c["id"] == "vs_hot_reload_max_velocity_effect")["detail"].update(
                peak_output=0.50),
        "startup_readback_mismatch": lambda r: next(
            c for c in r["checks"] if c["id"] == "vs_startup_readback")["detail"]["readback"].update(
                max_velocity=[0.50, 0.0, 2.50]),
        "over_cap_pass_claim": lambda r: next(
            c for c in r["checks"] if c["id"] == "vs_over_cap_input_clamped")["detail"].update(
                peak_linear=0.90),
        "inconsistent_summary": lambda r: r["summary"].update(failed=0, result="pass", checks=99),
        "proportional_creep": lambda r: next(
            c for c in r["checks"] if c["id"] == "vs_proportional_small_target_no_creep")[
                "detail"].update(late_window_max=0.30),
    }
    for name, mutate in shims.items():
        candidate = json.loads(json.dumps(base))
        mutate(candidate)
        if not validate_receipt(candidate):
            failures.append(f"shim '{name}' was incorrectly accepted")

    stale = json.loads(json.dumps(base))
    stale["generated_at"] = "2000-01-01T00:00:00Z"
    if not validate_receipt(stale, max_age_seconds=3600):
        failures.append("stale receipt was incorrectly accepted")

    try:
        assert_isolated_environment({"patrolbot_isolated_test": "1", "ros_domain_id": "188",
                                     "ros_localhost_only": "1", "interfaces": ["lo", "eth0"]})
        failures.append("isolation gate accepted a non-loopback environment")
    except RuntimeError:
        pass
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", nargs="?", help="path to receipt.json")
    parser.add_argument("--max-age-seconds", type=float, default=None)
    parser.add_argument("--require-isolation", dest="require_isolation", action="store_true",
                        default=True)
    parser.add_argument("--allow-unisolated", dest="require_isolation", action="store_false")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        failures = self_test()
        print(json.dumps({"self_test_passed": not failures, "failures": failures}, indent=2))
        return 0 if not failures else 1

    if not args.receipt:
        parser.error("a receipt path is required (or --self-test)")

    path = Path(args.receipt)
    try:
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            raise ValueError(f"receipt exceeds {MAX_RECEIPT_BYTES} bytes")
        receipt = json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"passed": False, "errors": [f"cannot read receipt: {error}"]}, indent=2))
        return 1

    if args.require_isolation:
        try:
            assert_isolated_environment()
        except RuntimeError as error:
            print(json.dumps({"passed": False, "errors": [str(error)]}, indent=2))
            return 1

    errors = validate_receipt(receipt, max_age_seconds=args.max_age_seconds)
    verdict = {
        "passed": not errors,
        "errors": errors,
        "check_result": (receipt.get("summary") or {}).get("result"),
        "physical_acceptance": receipt.get("physical_acceptance"),
    }
    print(json.dumps(verdict, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
