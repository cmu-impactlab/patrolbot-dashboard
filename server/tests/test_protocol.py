import json
import pathlib

import pytest

from app.protocol import decode, encode, decode_rle, encode_rle
from app.protocol.envelope import ProtocolError

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "shared" / "schemas" / "fixtures"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.name)
def test_fixture_round_trip(path):
    raw = path.read_text()
    envelope, payload = decode(raw)
    assert envelope.type == path.stem
    # Re-encode and decode again: stable.
    frame = encode(envelope.type, envelope.robot_id, envelope.sequence, payload, envelope.timestamp)
    envelope2, payload2 = decode(frame)
    assert envelope2.type == envelope.type
    assert payload2 == payload


def test_unknown_type_rejected():
    frame = json.dumps({"version": 1, "type": "telemetry.bogus", "robot_id": "r", "sequence": 1,
                        "timestamp": "2026-07-17T00:00:00Z", "data": {}})
    with pytest.raises(ProtocolError):
        decode(frame)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_pose_rejects_non_finite(bad):
    frame = encode("telemetry.pose", "patrolbot-01", 1, {
        "x": bad, "y": 0.0, "yaw": 0.0, "linear_velocity": 0.0, "angular_velocity": 0.0,
    })
    with pytest.raises(ProtocolError):
        decode(frame)


def test_legacy_pose_uncertainty_fields_are_unavailable():
    _, pose = decode(encode("telemetry.pose", "r", 1, {
        "x": 0, "y": 0, "yaw": 0, "linear_velocity": 0, "angular_velocity": 0,
    }))
    assert pose.planar_covariance is None
    assert pose.covariance_limit is None
    assert pose.localization_age_s is None
    assert pose.localization_max_age_s is None


def test_pose_uncertainty_nullable_diagonal_round_trip():
    _, pose = decode(encode("telemetry.pose", "r", 1, {
        "x": 0, "y": 0, "yaw": 0, "linear_velocity": 0, "angular_velocity": 0,
        "planar_covariance": [0.01, None, -1e-9], "covariance_limit": 0.25,
        "localization_age_s": 12.5, "localization_max_age_s": 30,
    }))
    assert pose.planar_covariance == (0.01, None, -1e-9)
    assert pose.covariance_limit == 0.25
    assert pose.localization_age_s == 12.5
    assert pose.localization_max_age_s == 30


@pytest.mark.parametrize("fields", [
    {"planar_covariance": [0.1, 0.1]},
    {"planar_covariance": [0.1, float("inf"), 0.1]},
    {"covariance_limit": float("nan")},
    {"localization_age_s": float("inf")},
    {"localization_max_age_s": float("nan")},
])
def test_pose_rejects_malformed_uncertainty_fields(fields):
    with pytest.raises(ProtocolError):
        decode(encode("telemetry.pose", "r", 1, {
            "x": 0, "y": 0, "yaw": 0, "linear_velocity": 0, "angular_velocity": 0,
            **fields,
        }))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["voltage", "current", "percentage"])
def test_battery_rejects_non_finite(field, bad):
    """Python's json module emits bare NaN/Infinity tokens, which the browser's
    JSON.parse refuses — one such frame would break the socket for every
    widget, not just the battery one. Reject at decode instead."""
    data = {"voltage": 24.6, "current": -1.5, "percentage": 88.0, "charging": False}
    data[field] = bad
    with pytest.raises(ProtocolError):
        decode(encode("telemetry.battery", "patrolbot-01", 1, data))


def test_battery_accepts_null_optionals():
    """The nullable fields must still be nullable — the bridge maps a
    non-finite current/percentage to null rather than dropping the sample."""
    _, payload = decode(encode("telemetry.battery", "patrolbot-01", 1, {
        "voltage": 24.6, "current": None, "percentage": None, "charging": False,
    }))
    assert payload.current is None and payload.percentage is None


def test_legacy_base_state_parses_epoch_fields_fail_closed():
    _, payload = decode(encode("telemetry.base_state", "patrolbot-01", 1, {
        "session_generation": 1, "link_connected": True,
        "telemetry_age": 0.1, "hardware_state_valid": True,
        "charge_state": "idle", "motors_enabled": True,
        "estop_pressed": False, "fault_flags": 0, "stall_value": 0,
        "bumpers_front": False, "bumpers_rear": False,
    }))
    assert payload.odom_epoch_valid is False
    assert payload.localization_recovery_required is True
    assert payload.localization_recovery_stage == ""
    assert payload.localization_seed_stamp_ns == 0


def test_wrong_version_rejected():
    frame = json.dumps({"version": 2, "type": "telemetry.heartbeat", "robot_id": "r", "sequence": 1,
                        "timestamp": "2026-07-17T00:00:00Z", "data": {"uptime_s": 1.0}})
    with pytest.raises(ProtocolError):
        decode(frame)


def test_rle_round_trip():
    cells = [-1] * 10 + [0] * 5 + [100] + [0] * 3 + [-1]
    assert decode_rle(encode_rle(cells)) == cells
    assert encode_rle([]) == []
    assert decode_rle([]) == []


def test_map_fixture_rle_matches_dimensions():
    raw = json.loads((FIXTURES / "telemetry.map.json").read_text())
    data = raw["data"]
    cells = decode_rle(data["rle"])
    assert len(cells) == data["width"] * data["height"]
