"""Shared builders for robot-tuning tests.

Kept out of ``conftest`` (and off the ``test_`` prefix) so it is importable as a
plain sibling module without being collected as a test file. Everything here
mirrors the canonical wire shapes the broker/API validate.
"""
from __future__ import annotations

import uuid

CONTRACT = "patrolbot-robot-tuning"
CONTRACT_VERSION = 1
CAPABILITY = "robot_tuning_v1"
READ_CAPABILITY = "robot_tuning_read_v1"


def new_uuid() -> str:
    return str(uuid.uuid4())


def generation(session_id: str | None = None) -> dict:
    return {"capability": CAPABILITY,
            "session_id": session_id or new_uuid()}


def request(operation: str = "read", *, request_id: str | None = None,
            session_id: str | None = None, **extra) -> dict:
    """A structurally valid canonical request for ``operation``."""
    payload = {
        "contract": CONTRACT,
        "version": CONTRACT_VERSION,
        "operation": operation,
        "request_id": request_id or new_uuid(),
        "generation": generation(session_id),
    }
    payload.update(extra)
    return payload


def read_request(**extra) -> dict:
    return request(**{"operation": "read", **extra})


def status_request(*, job_id: str | None = None, **extra) -> dict:
    payload = request(**{"operation": "status", **extra})
    if job_id is not None:
        payload["job_id"] = job_id
    return payload


def save_request(changes: list[dict], *, base_revision: int = 1, **extra) -> dict:
    return request(**{"operation": "save", "base_revision": base_revision,
                      "changes": changes, **extra})


def apply_request(*, expected_revision: int = 1, **extra) -> dict:
    return request(**{"operation": "apply",
                      "expected_revision": expected_revision, **extra})


def redeploy_request(*, expected_revision: int = 1, **extra) -> dict:
    return request(**{"operation": "redeploy",
                      "expected_revision": expected_revision, **extra})


def restore_request(*, expected_revision: int = 1, restore_revision: int = 0,
                    target: str = "last_known_good", **extra) -> dict:
    return request(**{"operation": "restore", "expected_revision": expected_revision,
                      "restore_revision": restore_revision, "target": target,
                      **extra})


def change(pid: str, value) -> dict:
    return {"id": pid, "value": value}


def snapshot(**over) -> dict:
    """A valid runtime snapshot in the coordinator's runtime shape."""
    data = {
        "session_id": new_uuid(),
        "desired_revision": 3,
        "active_revision": 2,
        "desired": {"max_linear_m_s": 0.2},
        "active": {"max_linear_m_s": 0.2},
        "active_known": True,
        "effective_limits": {"max_linear_m_s": 0.3, "max_angular_rad_s": 0.7},
        "pending_keys": [],
        "catalog": [{"id": "max_linear_m_s", "type": "number"}],
        "writable": True,
        "activation_supported": False,
        "safe_apply": {"allowed": True, "reason": ""},
        "latest_job": None,
        "digest": None,
        "compatibility": "ok",
    }
    data.update(over)
    return data


def response(request_id: str, outcome: str = "ok", *, detail: str = "",
             snapshot_data: dict | None = None, job: dict | None = None) -> dict:
    return {"request_id": request_id, "outcome": outcome, "detail": detail,
            "snapshot": snapshot_data, "job": job}


def actor(role: str = "administrator", *, user_id: int = 7,
          username: str = "adm") -> dict:
    return {"id": user_id, "username": username, "role": role}
