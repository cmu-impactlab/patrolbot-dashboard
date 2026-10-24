"""Strict validation of the canonical tuning request/snapshot/reply shapes.

These are the pure, dependency-free checks that run on the server before a
frame is forwarded. They are neither authorization nor proof of safe
activation: they only reject malformed, unknown, protected, non-finite and
out-of-range content, and refuse to pass a snapshot claiming state the reviewed
catalog does not allow.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from app.tuning import validation
from app.tuning.errors import TuningInvalid, TuningRegistryError

from tuning_fixtures import (
    actor,
    apply_request,
    change,
    generation,
    new_uuid,
    read_request,
    redeploy_request,
    request,
    restore_request,
    save_request,
    snapshot,
    status_request,
)


# -- registry loading -------------------------------------------------------

def test_reviewed_registry_loads_with_parameters():
    registry = validation.load_registry()
    assert registry["contract"] == validation.CONTRACT
    index = validation.registry_index(registry)
    assert len(index) == 37
    assert "max_linear_m_s" in index


def test_missing_registry_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(validation, "_registry_paths",
                        lambda: (tmp_path / "nope.json",))
    with pytest.raises(TuningRegistryError):
        validation.load_registry(force=True)


def test_corrupt_registry_fails_closed(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"contract": "something-else", "version": 1}))
    monkeypatch.setenv("PATROLBOT_TUNING_REGISTRY", str(bad))
    with pytest.raises(TuningRegistryError):
        validation.load_registry(force=True)


# -- primitives -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (1, True), (True, False), (1.0, False), ("1", False), (None, False),
])
def test_is_strict_int_rejects_bool_and_non_int(value, expected):
    assert validation.is_strict_int(value) is expected


@pytest.mark.parametrize("value,expected", [
    (0, True), (0.0, True), (1e9, True), (float("inf"), False),
    (float("nan"), False), (True, False), ("1", False), (None, False),
    (2 ** 64, False),
])
def test_is_finite_number_bounds_and_types(value, expected):
    assert validation.is_finite_number(value) is expected


@pytest.mark.parametrize("value,expected", [
    (new_uuid(), True),
    ("00000000-0000-0000-0000-000000000000", True),
    ("ABC00000-0000-0000-0000-000000000000", False),  # uppercase
    ("not-a-uuid", False), (None, False), (123, False),
])
def test_canonical_uuid(value, expected):
    assert (validation.canonical_uuid(value) is not None) is expected


# -- validate_request: happy paths ------------------------------------------

@pytest.mark.parametrize("payload", [
    read_request(),
    status_request(),
    status_request(job_id=new_uuid()),
    save_request([change("amcl.alpha1", 0.1)]),
    apply_request(expected_revision=2),
    redeploy_request(expected_revision=2),
    restore_request(expected_revision=2, restore_revision=1),
])
def test_valid_requests_pass(payload):
    assert validation.validate_request(payload) is payload


@pytest.mark.parametrize("builder", [apply_request, redeploy_request, restore_request])
def test_reviewed_binding_is_validated_without_exposing_node_or_service_inputs(builder):
    request = builder(review={"digest": "sha256:" + "a" * 64, "plan_hash": "sha256:" + "b" * 64})
    assert validation.validate_request(request) is request
    request["review"]["node"] = "amcl"
    with pytest.raises(TuningInvalid):
        validation.validate_request(request)


def _with_preview():
    data = snapshot(committed_active_revision=2, desired_digest="sha256:" + "a" * 64,
                    digest="sha256:" + "b" * 64, desired={"amcl.alpha1": .06})
    plan = {"version": 1, "operation": "apply", "revision": 3, "desired_revision": 3,
            "base_revision": 2, "digest": data["desired_digest"], "base_digest": data["digest"],
            "session_id": data["session_id"], "hardware_generation": "sha256:" + "c" * 64,
            "execution_kind": "scoped_lifecycle", "nodes": ["amcl"], "services": [],
            "seed_required": True, "disruption": "AMCL reset; operator seed required.",
            "executable": False, "reasons": [{"code": "implementation_unverified", "detail": "Native proof required."}],
            "changes": [{"id": "amcl.alpha1", "before": .05, "after": .06}]}
    plan["plan_hash"] = "sha256:" + hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data["activation_plan"] = plan
    return data


def test_core_preview_is_displayable_but_does_not_claim_support_or_activity():
    data = _with_preview()
    assert validation.validate_snapshot(data) is data
    assert not data["activation_supported"]


def _rehash(plan):
    content = {key: value for key, value in plan.items() if key != 'plan_hash'}
    plan['plan_hash'] = 'sha256:' + hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _with_nav2_preview():
    data = _with_preview()
    runtime = json.loads((Path(__file__).resolve().parents[2] / 'contracts/robot-tuning-runtime.json').read_text())
    data['activation_plan'].update(strategy='hot_or_nav2_redeploy', execution_kind='nav2_redeploy',
        nodes=runtime['nav2_redeploy']['nodes'], services=['navigation'], image_rebuild=False,
        disruption='Restart the complete navigation container; a fresh operator seed is required.')
    _rehash(data['activation_plan'])
    return data


def test_whole_nav2_review_is_displayable_without_claiming_enabled_native_support():
    data = _with_nav2_preview()
    assert validation.validate_snapshot(data) is data
    assert not data['activation_supported']


@pytest.mark.parametrize('field,value', [('services', ['navigation', 'bridge']),
    ('services', ['navigation', 'bringup']), ('services', ['bridge']), ('image_rebuild', True),
    ('seed_required', False), ('strategy', 'robot_docker'), ('nodes', ['amcl'])])
def test_rehashed_preview_cannot_rename_broad_restart_or_omit_nav2_seed_loss(field, value):
    data = _with_nav2_preview()
    data['activation_plan'][field] = value
    _rehash(data['activation_plan'])  # Valid hash is not enough to make a scope valid.
    with pytest.raises(TuningInvalid):
        validation.validate_snapshot(data)


@pytest.mark.parametrize("part,value", [("nodes", ["/bin/sh"]), ("services", ["host-reset"]),
    ("seed_required", False), ("desired_revision", 4), ("session_id", new_uuid()), ("executable", True)])
def test_modified_or_unbound_preview_is_refused(part, value):
    data = _with_preview()
    data["activation_plan"][part] = value
    with pytest.raises(TuningInvalid):
        validation.validate_snapshot(data)


# -- validate_request: structural refusals -----------------------------------

def test_non_object_request_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(["not", "an", "object"])
    assert excinfo.value.code == "bad_type"


@pytest.mark.parametrize("operation", ["delete", "raw", "write", None, 1])
def test_unsupported_operation_rejected(operation):
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(read_request(operation=operation))
    assert excinfo.value.code == "unsupported_operation"


def test_unknown_field_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(read_request(actor={"role": "administrator"}))
    assert excinfo.value.code == "unknown_field"


def test_missing_field_rejected():
    payload = save_request([change("max_linear_m_s", 0.2)])
    del payload["changes"]
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(payload)
    assert excinfo.value.code == "missing_field"


@pytest.mark.parametrize("key,value", [
    ("contract", "other"), ("version", 2),
])
def test_wrong_contract_or_version_rejected(key, value):
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(read_request(**{key: value}))
    assert excinfo.value.code == "bad_const"


def test_non_canonical_request_id_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(read_request(request_id="not-a-uuid"))
    assert excinfo.value.code == "non_canonical_uuid"


def test_generation_must_be_exact_shape():
    payload = read_request()
    payload["generation"]["extra"] = 1
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(payload)
    assert excinfo.value.code == "unknown_field"


def test_generation_capability_and_session_checked():
    bad_cap = read_request()
    bad_cap["generation"]["capability"] = "other"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(bad_cap)
    assert excinfo.value.code == "bad_const"

    bad_session = read_request()
    bad_session["generation"]["session_id"] = "nope"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(bad_session)
    assert excinfo.value.code == "bad_pattern"


def test_deep_nesting_rejected():
    node: dict = {}
    cursor = node
    for _ in range(validation.MAX_DEPTH + 2):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(read_request(extra=node))
    # `extra` is an unknown field, but the bounded scan rejects first.
    assert excinfo.value.code in ("payload_too_deep", "unknown_field")


def test_non_finite_number_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(
            [change("max_linear_m_s", float("inf"))]))
    assert excinfo.value.code == "non_finite_number"


# -- validate_request: change semantics --------------------------------------

def test_unknown_logical_id_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("nope.nope", 1)]))
    assert excinfo.value.code == "unknown_logical_id"


def test_path_injection_in_change_id_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("../../etc/passwd", 1)]))
    assert excinfo.value.code == "path_injection"


def test_protected_parameter_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(
            [change("safety_watchdog.scan_timeout", 1.0)]))
    assert excinfo.value.code == "protected_parameter"


def test_not_applicable_parameter_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("amcl.alpha5", 0.1)]))
    assert excinfo.value.code == "not_applicable"


def test_string_value_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("amcl.z_hit", "0.5")]))
    assert excinfo.value.code == "bad_type"


def test_boolean_as_number_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("max_linear_m_s", True)]))
    assert excinfo.value.code == "bool_as_number"


def test_integer_field_rejects_fractional_number():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(
            [change("amcl.min_particles", 1000.5)]))
    assert excinfo.value.code == "bad_type"


def test_out_of_bounds_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([change("max_linear_m_s", 0.5)]))
    assert excinfo.value.code == "out_of_bounds"


def test_duplicate_change_id_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([
            change("max_linear_m_s", 0.1), change("max_linear_m_s", 0.2)]))
    assert excinfo.value.code == "duplicate_change_id"


def test_empty_and_oversized_changes_lists_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([]))
    assert excinfo.value.code == "out_of_bounds"
    too_many = [change("max_linear_m_s", 0.1)
                for _ in range(validation.MAX_CHANGES + 1)]
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(too_many))
    assert excinfo.value.code == "out_of_bounds"


def test_dependency_min_le_max_enforced():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(
            [change("amcl.min_particles", 5000)]))
    assert excinfo.value.code == "dependency_violation"


def test_dependency_sum_equals_enforced():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request([
            change("amcl.z_hit", 0.5), change("amcl.z_rand", 0.6)]))
    assert excinfo.value.code == "dependency_violation"
    # A coherent pair passes.
    validation.validate_request(save_request([
        change("amcl.z_hit", 0.7), change("amcl.z_rand", 0.3)]))


def test_revision_must_be_js_safe_non_negative_int():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(apply_request(expected_revision=-1))
    assert excinfo.value.code == "out_of_bounds"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(save_request(
            [change("max_linear_m_s", 0.1)], base_revision=True))
    assert excinfo.value.code == "bad_type"


def test_restore_constraints():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(
            restore_request(expected_revision=1, restore_revision=2))
    assert excinfo.value.code == "out_of_bounds"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(restore_request(target="factory"))
    assert excinfo.value.code == "bad_const"


def test_status_job_id_must_be_canonical():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_request(status_request(job_id="nope"))
    assert excinfo.value.code == "non_canonical_uuid"


# -- validate_snapshot -------------------------------------------------------

def test_valid_snapshot_passes():
    data = snapshot()
    assert validation.validate_snapshot(data) is data


def test_snapshot_session_and_revision_types():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(session_id="nope"))
    assert excinfo.value.code == "non_canonical_uuid"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(desired_revision=True))
    assert excinfo.value.code == "bad_type"


def test_snapshot_unknown_desired_id_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(desired={"nope.nope": 1}))
    assert excinfo.value.code == "unknown_logical_id"


def test_snapshot_desired_out_of_bounds_rejected():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(desired={"max_linear_m_s": 0.9}))
    assert excinfo.value.code == "out_of_bounds"


def test_snapshot_active_unknown_requires_null_values():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(active_known=False,
                                              active={"max_linear_m_s": 0.2}))
    assert excinfo.value.code == "inconsistent_active_state"
    # Null active values with active_known False is honest and passes.
    validation.validate_snapshot(snapshot(active_known=False,
                                          active={"max_linear_m_s": None},
                                          active_revision=None, effective_limits=None))


def test_snapshot_active_allows_drift_but_checks_type():
    # A real readback may drift outside current policy: type only.
    validation.validate_snapshot(snapshot(active={"max_linear_m_s": 1.5}))
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(active={"max_linear_m_s": "fast"}))
    assert excinfo.value.code == "bad_type"


@pytest.mark.parametrize("limits", [
    {"max_linear_m_s": 0.31, "max_angular_rad_s": 0.7},
    {"max_linear_m_s": 0.3, "max_angular_rad_s": 0.71},
    {"max_linear_m_s": -0.1, "max_angular_rad_s": 0.7},
    {"max_linear_m_s": 0.3},
])
def test_snapshot_effective_limits_bounded(limits):
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(effective_limits=limits))
    assert excinfo.value.code in ("out_of_bounds", "bad_type")


def test_snapshot_pending_keys_checked():
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_snapshot(snapshot(pending_keys=["nope.nope"]))
    assert excinfo.value.code == "unknown_logical_id"


@pytest.mark.parametrize("over", [
    {"safe_apply": {"allowed": "yes", "reason": ""}},
    {"safe_apply": {"allowed": True}},
    {"digest": "nothex"},
    {"compatibility": "x" * 201},
    {"writable": "yes"},
    {"active_known": "yes"},
    {"activation_supported": "yes"},
    {"catalog": [{"nope": 1}]},
])
def test_snapshot_malformed_fields_rejected(over):
    with pytest.raises(TuningInvalid):
        validation.validate_snapshot(snapshot(**over))


# -- validate_actor ----------------------------------------------------------

def test_actor_valid_and_rejects_smuggled_shape():
    assert validation.validate_actor(actor()) == actor()
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_actor({"id": 1, "username": "a", "role": "administrator",
                                   "is_admin": True})
    assert excinfo.value.code == "unknown_field"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_actor(actor(role="root"))
    assert excinfo.value.code == "bad_enum"
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_actor({"id": True, "username": "a",
                                   "role": "administrator"})
    assert excinfo.value.code == "bad_type"


# -- validate_response -------------------------------------------------------

def test_valid_response_normalized():
    rid = new_uuid()
    result = validation.validate_response(
        {"request_id": rid, "outcome": "ok", "detail": None})
    assert result == {"request_id": rid, "outcome": "ok", "detail": "",
                      "snapshot": None, "job": None}


@pytest.mark.parametrize("data,code", [
    ({"request_id": "nope", "outcome": "ok"}, "non_canonical_uuid"),
    ({"request_id": new_uuid(), "outcome": "weird"}, "bad_enum"),
    ({"request_id": new_uuid(), "outcome": "ok", "detail": "x" * 600}, "bad_type"),
    ({"request_id": new_uuid(), "outcome": "ok", "job": "nope"}, "bad_type"),
    ({"request_id": new_uuid(), "outcome": "ok",
      "snapshot": {"session_id": "nope"}}, "non_canonical_uuid"),
])
def test_bad_response_rejected(data, code):
    with pytest.raises(TuningInvalid) as excinfo:
        validation.validate_response(data)
    assert excinfo.value.code == code


def test_response_snapshot_validated_with_registry():
    rid = new_uuid()
    result = validation.validate_response(
        {"request_id": rid, "outcome": "ok", "snapshot": snapshot()})
    assert result["snapshot"]["session_id"]
