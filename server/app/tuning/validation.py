"""Strict, dependency-free validation for the robot-tuning wire contract.

This is the server-side pre-filter for canonical tuning requests and for the
runtime snapshot the robot returns. It is *not* authorization and it is not
proof of safe activation: it only rejects malformed, unknown, protected,
non-finite or out-of-range content before it is forwarded, and refuses to pass
a snapshot that claims state the contract does not allow.

Dependency choice: the production server image does not ship ``jsonschema``.
The reviewed catalog (``contracts/robot-tuning.registry.json``) is loaded as
plain JSON and the request/snapshot shapes are enforced with stdlib code plus
the existing Pydantic runtime. The vendored ``validate_robot_tuning.py``
remains the canonical offline validator; this module mirrors its important
semantics (unknown/protected/read-only ids, bool-as-number, finite numbers,
bounds, dependency coherence, integer revisions) without importing it, so the
server runtime does not depend on the dev/test-only contract requirements.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .errors import TuningInvalid, TuningRegistryError

CONTRACT = "patrolbot-robot-tuning"
CONTRACT_VERSION = 1
CAPABILITY = "robot_tuning_v1"
# Advertised by the robot bridge only when the core service is actually
# reachable. Reads need the read capability; mutations need full tuning.
READ_CAPABILITY = "robot_tuning_read_v1"
MUTATION_CAPABILITY = "robot_tuning_v1"

OPERATIONS = ("read", "save", "apply", "redeploy", "status", "restore")
MUTATION_OPERATIONS = frozenset({"save", "apply", "redeploy", "restore"})
READ_OPERATIONS = frozenset({"read", "status"})

# Wire bounds. The canonical request is small; the runtime snapshot is larger.
REQUEST_MAX_BYTES = 18 * 1024
RESPONSE_MAX_BYTES = 256 * 1024
MAX_DEPTH = 32
MAX_ITEMS = 8192
MAX_CHANGES = 32
MAX_DETAIL = 512
MAX_USERNAME = 128
JS_SAFE_INT = 2 ** 53 - 1
MAX_INT = 2 ** 63 - 1

LOGICAL_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_OUTCOMES = ("ok", "conflict", "refused", "failed", "unknown")
_ROLES = ("administrator", "operator", "observer")
_CLASSIFICATIONS = ("uncommissioned", "candidate_unverified",
                    "restart_required", "read_only")

# Candidate/installed locations for the reviewed catalog. Environment override
# first (tests/ops), then the production image path, then the checkout.
_REGISTRY_CANDIDATES: tuple[Path, ...] = ()
_REGISTRY_CACHE: dict[str, Any] | None = None


def _registry_paths() -> tuple[Path, ...]:
    override = os.environ.get("PATROLBOT_TUNING_REGISTRY", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.append(Path("/contracts/robot-tuning.registry.json"))
    candidates.append(Path(__file__).resolve().parents[3] / "contracts/robot-tuning.registry.json")
    return tuple(candidates)


def load_registry(*, force: bool = False) -> dict[str, Any]:
    """Load the reviewed registry, or raise :class:`TuningRegistryError`.

    Cached after the first successful read. A missing/corrupt file is a hard
    fail-closed error: a tuning endpoint must never operate without the
    reviewed logical catalog.
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None and not force:
        return _REGISTRY_CACHE
    for path in _registry_paths():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, TypeError) as exc:
            raise TuningRegistryError(
                f"Tuning catalog at {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("contract") != CONTRACT:
            raise TuningRegistryError(
                f"Tuning catalog at {path} is not the reviewed {CONTRACT} registry.")
        if data.get("version") != CONTRACT_VERSION:
            raise TuningRegistryError(
                f"Tuning catalog at {path} has an unsupported version.")
        index = registry_index(data)
        if not index:
            raise TuningRegistryError(
                f"Tuning catalog at {path} declares no parameters.")
        _REGISTRY_CACHE = data
        return data
    raise TuningRegistryError(
        "The reviewed tuning catalog is not installed on this server.")


def registry_index(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parameters = registry.get("parameters")
    index: dict[str, dict[str, Any]] = {}
    if isinstance(parameters, list):
        for entry in parameters:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                index[entry["id"]] = entry
    return index


def classify(entry: dict[str, Any]) -> str:
    """Mirror the slice-1 classification rules; never claims a live field."""
    if entry.get("activation_block") == "commissioned_usable_lower_bound_unknown":
        return "uncommissioned"
    if entry.get("protected") or entry.get("not_applicable"):
        return "read_only"
    if entry.get("mutability") == "restart_required":
        return "restart_required"
    return "candidate_unverified"


# ---------------------------------------------------------------------------
# Primitive checks
# ---------------------------------------------------------------------------

def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number literal: {name}")


def is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= MAX_INT
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def canonical_uuid(value: Any) -> str | None:
    if not isinstance(value, str) or not UUID_PATTERN.match(value):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return value if str(parsed) == value else None


def _has_path_injection(text: str) -> bool:
    return ".." in text or "/" in text or "\\" in text or "\x00" in text


def _is_logical_id(value: Any) -> bool:
    return (isinstance(value, str) and len(value) <= 64
            and bool(LOGICAL_ID.fullmatch(value)))


def _scan(value: Any, *, max_bytes: int, path: str = "") -> None:
    """Bounded depth/item/byte scan; raises TuningInvalid on violation."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    items = 0
    total = 0
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise TuningInvalid("The request is nested too deeply.", code="payload_too_deep")
        if isinstance(node, bool) or node is None:
            total += 8
        elif isinstance(node, float):
            total += 8
            if not math.isfinite(node):
                raise TuningInvalid("Non-finite numbers are not allowed.",
                                    code="non_finite_number")
        elif isinstance(node, int):
            total += 8
            if abs(node) > MAX_INT:
                raise TuningInvalid("Integer value out of range.", code="integer_too_large")
        elif isinstance(node, str):
            if len(node) > max_bytes:
                raise TuningInvalid("The request is too large.", code="payload_too_large")
            try:
                total += len(node.encode("utf-8"))
            except UnicodeError as exc:
                raise TuningInvalid("Invalid Unicode text.", code="bad_type") from exc
        elif isinstance(node, dict):
            total += 8
            for key, item in node.items():
                items += 1
                if items > MAX_ITEMS:
                    raise TuningInvalid("The request has too many fields.",
                                        code="payload_too_large")
                if not isinstance(key, str):
                    raise TuningInvalid("Object keys must be strings.", code="bad_type")
                if len(key) > max_bytes:
                    raise TuningInvalid("The request is too large.", code="payload_too_large")
                try:
                    total += len(key.encode("utf-8"))
                except UnicodeError as exc:
                    raise TuningInvalid("Invalid Unicode key.", code="bad_type") from exc
                stack.append((item, depth + 1))
        elif isinstance(node, (list, tuple)):
            total += 8
            for item in node:
                items += 1
                if items > MAX_ITEMS:
                    raise TuningInvalid("The request has too many items.",
                                        code="payload_too_large")
                stack.append((item, depth + 1))
        else:
            raise TuningInvalid(f"Unsupported value type {type(node).__name__}.",
                                code="bad_type")
        if total > max_bytes:
            raise TuningInvalid("The request is too large.", code="payload_too_large")


# ---------------------------------------------------------------------------
# Registry semantics
# ---------------------------------------------------------------------------

def _check_value(entry: dict[str, Any], value: Any, *, where: str,
                 check_bounds: bool = True) -> None:
    declared = entry.get("type")
    pid = entry.get("id", where)
    if isinstance(value, bool):
        if declared != "boolean":
            raise TuningInvalid(f"{pid} expects a {declared}, got a boolean.",
                                code="bool_as_number")
        return
    if declared in ("number", "integer"):
        if not is_finite_number(value):
            raise TuningInvalid(f"{pid} expects a finite {declared}.", code="bad_type")
        if declared == "integer" and not is_strict_int(value):
            raise TuningInvalid(f"{pid} expects an integer.", code="bad_type")
        if check_bounds:
            _check_bounds(entry, value)
        return
    raise TuningInvalid(f"{pid} expects a {declared}.", code="bad_type")


def _check_bounds(entry: dict[str, Any], value: Any) -> None:
    bounds = entry.get("bounds")
    if not isinstance(bounds, dict):
        return
    lower = bounds.get("lower")
    upper = bounds.get("upper")
    if is_finite_number(lower):
        if bounds.get("lower_inclusive", True):
            if value < lower:
                raise TuningInvalid(
                    f"{entry['id']} is below the reviewed lower bound {lower}.",
                    code="out_of_bounds")
        elif value <= lower:
            raise TuningInvalid(
                f"{entry['id']} must be greater than {lower}.", code="out_of_bounds")
    if is_finite_number(upper):
        if bounds.get("upper_inclusive", True):
            if value > upper:
                raise TuningInvalid(
                    f"{entry['id']} is above the reviewed upper bound {upper}.",
                    code="out_of_bounds")
        elif value >= upper:
            raise TuningInvalid(
                f"{entry['id']} must be less than {upper}.", code="out_of_bounds")


def _check_change(change: Any, index: dict[str, dict[str, Any]],
                  resolved: dict[str, Any]) -> str:
    if not isinstance(change, dict):
        raise TuningInvalid("Each change must be an object.", code="bad_type")
    extra = set(change) - {"id", "value"}
    if extra:
        raise TuningInvalid(f"Unknown change field(s): {sorted(extra)}.",
                            code="unknown_field")
    pid = change.get("id")
    if not isinstance(pid, str) or _has_path_injection(pid):
        raise TuningInvalid("change.id must be a logical identifier.",
                            code="path_injection")
    entry = index.get(pid)
    if entry is None:
        raise TuningInvalid(f"Unknown tuning parameter: {pid!r}.",
                            code="unknown_logical_id")
    if entry.get("protected"):
        raise TuningInvalid(f"{pid} is protected and never editable.",
                            code="protected_parameter")
    if entry.get("not_applicable"):
        raise TuningInvalid(f"{pid} does not apply to this robot.",
                            code="not_applicable")
    if not entry.get("accepts_edits"):
        raise TuningInvalid(f"{pid} is read-only.", code="not_editable")
    if "value" not in change:
        raise TuningInvalid(f"{pid} is missing a value.", code="missing_field")
    value = change["value"]
    if isinstance(value, str):
        raise TuningInvalid("Tuning values cannot be strings.", code="bad_type")
    _check_value(entry, value, where=pid)
    resolved[pid] = value
    return pid


def _dependency_edges(index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for pid, entry in index.items():
        for dep in entry.get("dependencies", []):
            if not isinstance(dep, dict):
                continue
            edges.append({
                "source": pid,
                "target": dep.get("target"),
                "kind": dep.get("kind"),
                "equals": dep.get("equals"),
                "tolerance": dep.get("tolerance", 1e-9),
            })
    return edges


def _resolved_value(pid: Any, resolved: dict[str, Any],
                    index: dict[str, dict[str, Any]]) -> Any:
    if pid in resolved:
        return resolved[pid]
    entry = index.get(pid)
    return None if entry is None else entry.get("default")


def _check_dependencies(changed: list[str], index: dict[str, dict[str, Any]],
                        resolved: dict[str, Any]) -> None:
    changed_set = set(changed)
    if not changed_set:
        return
    for edge in _dependency_edges(index):
        if edge["source"] not in changed_set and edge["target"] not in changed_set:
            continue
        left = _resolved_value(edge["source"], resolved, index)
        right = _resolved_value(edge["target"], resolved, index)
        if not (is_finite_number(left) and is_finite_number(right)):
            raise TuningInvalid(
                f"Dependency {edge['source']} -> {edge['target']} has a non-numeric operand.",
                code="invalid_dependency_operand")
        if edge["kind"] == "less_than_or_equal":
            if left > right:
                if edge["source"] not in changed_set and index[edge["source"]].get("value_kind") in ("speed", "angular_speed"):
                    # Logical producer targets are distinct from generated
                    # effective limits: the core compiler emits min(target,
                    # ceiling) for the complete speed group. Never rewrite a
                    # slower source target upward to match a ceiling.
                    continue
                raise TuningInvalid(
                    f"{edge['source']} must be <= {edge['target']}.", code="dependency_violation")
        elif edge["kind"] == "sum_equals":
            total = edge["equals"]
            if not is_finite_number(total) or abs((left + right) - total) > edge["tolerance"]:
                raise TuningInvalid(
                    f"{edge['source']} and {edge['target']} must be coherent.",
                    code="dependency_violation")


def _check_generation(payload: dict[str, Any]) -> dict[str, Any]:
    generation = payload.get("generation")
    if not isinstance(generation, dict):
        raise TuningInvalid("generation must be an object.", code="bad_type")
    if set(generation) != {"capability", "session_id"}:
        raise TuningInvalid("generation has unexpected fields.", code="unknown_field")
    if generation.get("capability") != CAPABILITY:
        raise TuningInvalid("generation.capability is not this contract's capability.",
                            code="bad_const")
    if canonical_uuid(generation.get("session_id")) is None:
        raise TuningInvalid("generation.session_id must be a canonical UUID.",
                            code="bad_pattern")
    return generation


def _check_revision(payload: dict[str, Any], key: str) -> int:
    if key not in payload:
        raise TuningInvalid(f"{key} is required.", code="missing_field")
    value = payload[key]
    if not is_strict_int(value):
        raise TuningInvalid(f"{key} must be an integer revision.", code="bad_type")
    if not 0 <= value <= JS_SAFE_INT:
        raise TuningInvalid(f"{key} must be a non-negative JS-safe revision.",
                            code="out_of_bounds")
    return value


_REQUIRED_KEYS = {
    "read": {"contract", "version", "operation", "request_id", "generation"},
    "status": {"contract", "version", "operation", "request_id", "generation"},
    "save": {"contract", "version", "operation", "request_id", "generation",
             "base_revision", "changes"},
    "apply": {"contract", "version", "operation", "request_id", "generation",
              "expected_revision"},
    "redeploy": {"contract", "version", "operation", "request_id", "generation",
                 "expected_revision"},
    "restore": {"contract", "version", "operation", "request_id", "generation",
                "expected_revision", "restore_revision", "target"},
}
_ALLOWED_KEYS = {
    "read": set(_REQUIRED_KEYS["read"]),
    "status": set(_REQUIRED_KEYS["status"]) | {"job_id"},
    "save": set(_REQUIRED_KEYS["save"]),
    "apply": set(_REQUIRED_KEYS["apply"]) | {"review"},
    "redeploy": set(_REQUIRED_KEYS["redeploy"]) | {"review"},
    "restore": set(_REQUIRED_KEYS["restore"]) | {"review"},
}


def validate_request(payload: Any, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a canonical tuning request; return it unchanged or raise.

    Raises :class:`TuningInvalid` for any structural/semantic problem. Never
    performs authorization and never implies the runtime can activate anything.
    """
    if not isinstance(payload, dict):
        raise TuningInvalid("The request must be a JSON object.", code="bad_type")
    _scan(payload, max_bytes=REQUEST_MAX_BYTES)

    operation = payload.get("operation")
    if operation not in OPERATIONS:
        raise TuningInvalid(f"Unsupported operation: {operation!r}.",
                            code="unsupported_operation")
    allowed = _ALLOWED_KEYS[operation]
    extra = set(payload) - allowed
    if extra:
        raise TuningInvalid(f"Unknown request field(s): {sorted(extra)}.",
                            code="unknown_field")
    missing = _REQUIRED_KEYS[operation] - set(payload)
    if missing:
        raise TuningInvalid(f"Missing request field(s): {sorted(missing)}.",
                            code="missing_field")
    if payload.get("contract") != CONTRACT:
        raise TuningInvalid("Wrong tuning contract identifier.", code="bad_const")
    if not is_strict_int(payload.get("version")) or payload["version"] != CONTRACT_VERSION:
        raise TuningInvalid("Unsupported tuning contract version.", code="bad_const")
    if canonical_uuid(payload.get("request_id")) is None:
        raise TuningInvalid("request_id must be a canonical lowercase UUID.",
                            code="non_canonical_uuid")

    index = registry_index(registry if registry is not None else load_registry())
    _check_generation(payload)
    if "review" in payload:
        review = payload["review"]
        if (not isinstance(review, dict) or set(review) != {"digest", "plan_hash"}
                or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                       for value in review.values())):
            raise TuningInvalid("The reviewed activation binding is invalid.", code="invalid_review")

    if operation == "save":
        _check_revision(payload, "base_revision")
        changes = payload.get("changes")
        if not isinstance(changes, list) or not (1 <= len(changes) <= MAX_CHANGES):
            raise TuningInvalid(f"changes must be a list of 1..{MAX_CHANGES} entries.",
                                code="out_of_bounds")
        resolved: dict[str, Any] = {}
        changed: list[str] = []
        seen: set[str] = set()
        for change in changes:
            pid = _check_change(change, index, resolved)
            if pid in seen:
                raise TuningInvalid(f"Duplicate change for {pid!r}.",
                                    code="duplicate_change_id")
            seen.add(pid)
            changed.append(pid)
        _check_dependencies(changed, index, resolved)
    elif operation in ("apply", "redeploy"):
        _check_revision(payload, "expected_revision")
    elif operation == "restore":
        expected = _check_revision(payload, "expected_revision")
        restore = _check_revision(payload, "restore_revision")
        if restore > expected:
            raise TuningInvalid("restore_revision must not exceed expected_revision.",
                                code="out_of_bounds")
        if payload.get("target") != "last_known_good":
            raise TuningInvalid("restore target must be last_known_good.", code="bad_const")
    elif operation == "status" and "job_id" in payload:
        if canonical_uuid(payload["job_id"]) is None:
            raise TuningInvalid("job_id must be a canonical lowercase UUID.",
                                code="non_canonical_uuid")
    return payload


# ---------------------------------------------------------------------------
# Runtime snapshot (truthful executive state, never browser defaults)
# ---------------------------------------------------------------------------

def _snapshot_scalar(entry: dict[str, Any], value: Any, *, check_bounds: bool) -> None:
    if value is None:
        return
    if entry.get("type") == "string":
        if not isinstance(value, str) or len(value) > 256 or entry.get("accepts_edits"):
            raise TuningInvalid("Read-only string state is invalid.", code="bad_type")
        return
    _check_value(entry, value, where=entry.get("id", ""), check_bounds=check_bounds)


def validate_snapshot(snapshot: Any, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the runtime snapshot returned by the robot core.

    Unknown logical ids, wrong types, non-finite numbers and out-of-envelope
    effective ceilings are rejected. A snapshot may legitimately report unknown
    runtime state (``active_known`` false / null active values); that is honest
    and is not rewritten here.
    """
    if not isinstance(snapshot, dict):
        raise TuningInvalid("snapshot must be an object.", code="bad_type")
    _scan(snapshot, max_bytes=RESPONSE_MAX_BYTES)
    index = registry_index(registry if registry is not None else load_registry())
    for name, operation in (("activation_plan", "apply"), ("restore_plan", "restore")):
        plan = snapshot.get(name)
        if plan is not None:
            _validate_activation_plan(plan, snapshot, index, operation)

    readback = snapshot.get("readback")
    if readback is not None:
        if (not isinstance(readback, dict) or set(readback) != {
                "source", "age_s", "hardware_session_generation", "values", "limits"}
                or readback["source"] != "ros_parameters"
                or not is_finite_number(readback["age_s"]) or not 0 <= readback["age_s"] <= 3
                or not is_strict_int(readback["hardware_session_generation"])
                or not 0 <= readback["hardware_session_generation"] <= JS_SAFE_INT
                or not isinstance(readback["values"], dict)):
            raise TuningInvalid("Parameter readback is invalid.", code="bad_type")
        for pid, value in readback["values"].items():
            if pid not in index:
                raise TuningInvalid("Unknown parameter readback id.", code="unknown_logical_id")
            _snapshot_scalar(index[pid], value, check_bounds=False)
        limits = readback["limits"]
        if limits is not None:
            if not isinstance(limits, dict) or set(limits) != {"max_linear_m_s", "max_angular_rad_s"}:
                raise TuningInvalid("Invalid observed speed limits.", code="bad_type")
            for pid, hard in (("max_linear_m_s", .30), ("max_angular_rad_s", .70)):
                value = limits[pid]
                if (not is_finite_number(value) or not 0 <= value <= hard
                        or value != readback["values"].get(pid)):
                    raise TuningInvalid("Observed cap lacks matching readback.", code="bad_type")
    reason = snapshot.get("write_disabled_reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_DETAIL):
        raise TuningInvalid("Invalid editing refusal reason.", code="bad_type")

    uninitialized = snapshot.get("session_id") is None and snapshot.get("desired_revision") is None
    if not uninitialized and canonical_uuid(snapshot.get("session_id")) is None:
        raise TuningInvalid("snapshot.session_id must be a canonical UUID.",
                            code="non_canonical_uuid")

    desired_revision = snapshot.get("desired_revision")
    if not uninitialized and (not is_strict_int(desired_revision) or not 0 <= desired_revision <= JS_SAFE_INT):
        raise TuningInvalid("snapshot.desired_revision must be a non-negative integer.",
                            code="bad_type")
    active_revision = snapshot.get("active_revision")
    if active_revision is not None and (
            not is_strict_int(active_revision) or not 0 <= active_revision <= JS_SAFE_INT):
        raise TuningInvalid("snapshot.active_revision must be null or a non-negative integer.",
                            code="bad_type")

    active_known = snapshot.get("active_known")
    if not isinstance(active_known, bool):
        raise TuningInvalid("snapshot.active_known must be a boolean.", code="bad_type")
    writable = snapshot.get("writable")
    if not isinstance(writable, bool):
        raise TuningInvalid("snapshot.writable must be a boolean.", code="bad_type")
    activation_supported = snapshot.get("activation_supported")
    if not isinstance(activation_supported, bool):
        raise TuningInvalid("snapshot.activation_supported must be a boolean.",
                            code="bad_type")

    desired = snapshot.get("desired")
    if not isinstance(desired, dict):
        raise TuningInvalid("snapshot.desired must be an object.", code="bad_type")
    for pid, value in desired.items():
        if not _is_logical_id(pid) or pid not in index:
            raise TuningInvalid(f"snapshot.desired has an unknown id {pid!r}.",
                                code="unknown_logical_id")
        _snapshot_scalar(index[pid], value, check_bounds=True)

    active = snapshot.get("active")
    if not isinstance(active, dict):
        raise TuningInvalid("snapshot.active must be an object.", code="bad_type")
    for pid, value in active.items():
        if not _is_logical_id(pid) or pid not in index:
            raise TuningInvalid(f"snapshot.active has an unknown id {pid!r}.",
                                code="unknown_logical_id")
        if value is None:
            continue
        # A real readback may drift outside current policy; type-check only.
        _snapshot_scalar(index[pid], value, check_bounds=False)
    if not active_known and any(value is not None for value in active.values()):
        raise TuningInvalid("snapshot.active must be null while active_known is false.",
                            code="inconsistent_active_state")
    if uninitialized and (active_known or writable or activation_supported or active_revision is not None):
        raise TuningInvalid("Uninitialized state cannot claim activity or writes.", code="inconsistent_active_state")

    limits = snapshot.get("effective_limits")
    if limits is not None and not active_known:
        raise TuningInvalid("Effective runtime caps require verified readback.", code="inconsistent_active_state")
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) != {"max_linear_m_s", "max_angular_rad_s"}:
            raise TuningInvalid("snapshot.effective_limits has an unexpected shape.",
                                code="bad_type")
        for key, hard in (("max_linear_m_s", 0.30), ("max_angular_rad_s", 0.70)):
            value = limits.get(key)
            if not is_finite_number(value) or not 0.0 <= value <= hard:
                raise TuningInvalid(
                    f"snapshot.effective_limits.{key} is outside the commissioned envelope.",
                    code="out_of_bounds")

    pending_keys = snapshot.get("pending_keys")
    if not isinstance(pending_keys, list):
        raise TuningInvalid("snapshot.pending_keys must be a list.", code="bad_type")
    for pid in pending_keys:
        if not _is_logical_id(pid) or pid not in index:
            raise TuningInvalid(f"snapshot.pending_keys has an unknown id {pid!r}.",
                                code="unknown_logical_id")

    catalog = snapshot.get("catalog")
    if not isinstance(catalog, list):
        raise TuningInvalid("snapshot.catalog must be a list.", code="bad_type")
    for entry in catalog:
        if not isinstance(entry, dict) or not _is_logical_id(entry.get("id")):
            raise TuningInvalid("snapshot.catalog contains an invalid entry.",
                                code="bad_type")

    safe_apply = snapshot.get("safe_apply")
    if (not isinstance(safe_apply, dict) or set(safe_apply) != {"allowed", "reason"}
            or not isinstance(safe_apply.get("allowed"), bool)
            or not isinstance(safe_apply.get("reason"), str)):
        raise TuningInvalid("snapshot.safe_apply is invalid.", code="bad_type")

    latest_job = snapshot.get("latest_job")
    if latest_job is not None and not isinstance(latest_job, dict):
        raise TuningInvalid("snapshot.latest_job must be null or an object.",
                            code="bad_type")

    digest = snapshot.get("digest")
    if digest is not None and (not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest.removeprefix("sha256:"))):
        raise TuningInvalid("snapshot.digest must be null or a sha256 hex digest.",
                            code="bad_type")

    compatibility = snapshot.get("compatibility")
    if not isinstance(compatibility, str) or len(compatibility) > 200:
        raise TuningInvalid("snapshot.compatibility must be a bounded string.",
                            code="bad_type")
    return snapshot


def _validate_activation_plan(plan, snapshot, index, operation):
    """A preview is display data, never authority; reject unbound/malformed scope."""
    nodes = {entry["source"]["node"] for entry in index.values() if entry.get("accepts_edits")}
    nodes.add("patrolbot_joy_teleop")
    def invalid():
        raise TuningInvalid("The robot's reviewed activation plan is invalid.", code="invalid_activation_plan")
    if not isinstance(plan, dict):
        invalid()
    if (type(plan.get("version")) is not int or plan["version"] != 1
            or plan.get("operation") != operation
            or plan.get("session_id") != snapshot.get("session_id")
            or canonical_uuid(plan.get("session_id")) is None
            or not isinstance(plan.get("hardware_generation"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan["hardware_generation"])
            or type(plan.get("executable")) is not bool or type(plan.get("seed_required")) is not bool
            or plan.get("execution_kind") not in ("scoped_parameters", "scoped_lifecycle", "robot_docker", "nav2_redeploy")
            or not isinstance(plan.get("disruption"), str) or len(plan["disruption"]) > MAX_DETAIL):
        invalid()
    for key in ("revision", "desired_revision", "base_revision"):
        if not is_strict_int(plan.get(key)) or not 0 <= plan[key] <= JS_SAFE_INT:
            invalid()
    if (plan["desired_revision"] != snapshot.get("desired_revision")
            or plan["base_revision"] != snapshot.get("committed_active_revision")
            or plan["revision"] != snapshot.get("desired_revision" if operation == "apply" else "last_known_good_revision")):
        invalid()
    for key in ("digest", "base_digest", "plan_hash"):
        if not isinstance(plan.get(key), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan[key]):
            invalid()
    if plan["base_digest"] != snapshot.get("digest"):
        invalid()
    if operation == "apply" and plan["digest"] != snapshot.get("desired_digest"):
        invalid()
    if plan.get('execution_kind') == 'nav2_redeploy':
        # This is a fixed navigation-container scope, not arbitrary caller
        # selected services or a rename of the old whole-robot Docker fallback.
        native_nodes = {'amcl', 'behavior_server', 'bt_navigator', 'collision_monitor', 'controller_server',
            'joy_node', 'laser_static_tf', 'lifecycle_manager_localization', 'lifecycle_manager_navigation',
            'map_server', 'nav2_container', 'patrolbot_controller_feedback', 'patrolbot_dock_manager',
            'patrolbot_joy_teleop', 'patrolbot_safety_watchdog', 'planner_server', 'route_server',
            'smoother_server', 'velocity_smoother', 'waypoint_follower'}
        nodes |= native_nodes
        if (plan.get('strategy') != 'hot_or_nav2_redeploy' or plan.get('services') != ['navigation']
                or plan.get('seed_required') is not True or plan.get('image_rebuild') is not False
                or not isinstance(plan.get('nodes'), list)
                or not native_nodes <= set(item for item in plan['nodes'] if isinstance(item, str))):
            invalid()
    for key, allowed in (("nodes", nodes), ("services", {"bringup", "bridge", "navigation", "web-bridge"})):
        values = plan.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) or item not in allowed for item in values) or len(set(values)) != len(values):
            invalid()
    reasons = plan.get("reasons")
    if not isinstance(reasons, list) or len(reasons) > 32:
        invalid()
    for reason in reasons:
        if (not isinstance(reason, dict) or set(reason) != {"code", "detail"}
                or not isinstance(reason["code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason["code"])
                or not isinstance(reason["detail"], str) or len(reason["detail"]) > MAX_DETAIL):
            invalid()
    if reasons and plan["executable"]:
        invalid()
    changes = plan.get("changes")
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        invalid()
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"id", "before", "after"}:
            invalid()
        pid = change["id"]
        if (not isinstance(pid, str) or pid not in index or pid in seen or not index[pid].get("accepts_edits")
                or index[pid].get("protected") or index[pid].get("not_applicable") or change["after"] is None):
            invalid()
        seen.add(pid)
        _snapshot_scalar(index[pid], change["before"], check_bounds=False)
        _snapshot_scalar(index[pid], change["after"], check_bounds=True)
        if operation == "apply" and change["after"] != snapshot.get("desired", {}).get(pid):
            invalid()
    # The core compares this same canonical hash before executing. Relays must
    # not rewrite a node list, disruption or seed consequence after review.
    canonical = json.dumps({key: value for key, value in plan.items() if key != "plan_hash"},
                           sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if plan["plan_hash"] != "sha256:" + hashlib.sha256(canonical).hexdigest():
        invalid()


def validate_actor(actor: Any) -> dict[str, Any]:
    """Validate a server-derived actor block (defense in depth for the robot)."""
    if not isinstance(actor, dict):
        raise TuningInvalid("actor must be an object.", code="bad_type")
    if set(actor) != {"id", "username", "role"}:
        raise TuningInvalid("actor has unexpected fields.", code="unknown_field")
    if not is_strict_int(actor.get("id")):
        raise TuningInvalid("actor.id must be an integer.", code="bad_type")
    username = actor.get("username")
    if not isinstance(username, str) or not 1 <= len(username) <= MAX_USERNAME:
        raise TuningInvalid("actor.username must be a bounded string.", code="bad_type")
    if actor.get("role") not in _ROLES:
        raise TuningInvalid("actor.role is not a known role.", code="bad_enum")
    return actor


def validate_response(data: Any, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a decoded tuning.response body. Returns a normalized dict.

    Raises :class:`TuningInvalid` when the robot reply is malformed. The caller
    decides whether an unvalidated reply is an unknown outcome rather than a
    fabricated success.
    """
    if not isinstance(data, dict):
        raise TuningInvalid("The tuning reply must be an object.", code="bad_type")
    _scan(data, max_bytes=RESPONSE_MAX_BYTES)
    request_id = canonical_uuid(data.get("request_id"))
    if request_id is None:
        raise TuningInvalid("The tuning reply has no canonical request_id.",
                            code="non_canonical_uuid")
    outcome = data.get("outcome")
    if outcome not in _OUTCOMES:
        raise TuningInvalid(f"Unknown tuning outcome {outcome!r}.", code="bad_enum")
    detail = data.get("detail", "")
    if detail is None:
        detail = ""
    if not isinstance(detail, str) or len(detail) > MAX_DETAIL:
        raise TuningInvalid("The tuning reply detail is not a bounded string.",
                            code="bad_type")
    snapshot = data.get("snapshot")
    if snapshot is not None:
        validate_snapshot(snapshot, registry)
    job = data.get("job")
    if job is not None and not isinstance(job, dict):
        raise TuningInvalid("The tuning reply job must be null or an object.",
                            code="bad_type")
    return {"request_id": request_id, "outcome": outcome, "detail": detail,
            "snapshot": snapshot, "job": job}
