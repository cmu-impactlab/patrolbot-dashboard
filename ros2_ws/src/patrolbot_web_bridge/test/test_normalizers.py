"""Pure-function tests — run with plain pytest, no ROS graph required."""
import json
import math
import pathlib
from types import SimpleNamespace as NS

import pytest

from patrolbot_web_bridge import normalizers

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "shared" / "schemas" / "fixtures"


def quaternion(yaw):
    return NS(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2))


def make_odom(x=1.0, y=2.0, yaw=0.5, vx=0.3, wz=0.1):
    return NS(
        pose=NS(pose=NS(position=NS(x=x, y=y), orientation=quaternion(yaw))),
        twist=NS(twist=NS(linear=NS(x=vx), angular=NS(z=wz))),
    )


def make_amcl(x=1.5, y=2.5, yaw=1.0, var=0.01):
    cov = [0.0] * 36
    cov[0] = cov[7] = cov[35] = var
    return NS(pose=NS(pose=NS(position=NS(x=x, y=y), orientation=quaternion(yaw)),
                      covariance=cov))


def test_pose_prefers_amcl_with_odom_twist():
    pose = normalizers.normalize_pose(make_amcl(), make_odom())
    assert pose["frame_id"] == "map"
    assert pose["x"] == 1.5
    assert pose["linear_velocity"] == 0.3
    assert pose["localized"] is True
    assert abs(pose["yaw"] - 1.0) < 1e-3


def test_pose_degrades_to_odom_unlocalized():
    pose = normalizers.normalize_pose(None, make_odom())
    assert pose["frame_id"] == "odom"
    assert pose["localized"] is False


def test_pose_uncertain_when_covariance_high():
    pose = normalizers.normalize_pose(make_amcl(var=0.5), make_odom())
    assert pose["localized"] is False
    assert pose["covariance_trace"] > 0.25


def test_pose_payload_is_json_serializable_with_numpy_covariance():
    """The real robot's AMCL covariance is numpy-backed; the derived
    `localized` used to be a numpy bool_, which json.dumps cannot encode."""
    np = pytest.importorskip("numpy")
    amcl = make_amcl(var=0.01)
    amcl.pose.covariance = np.array(amcl.pose.covariance, dtype=np.float64)
    pose = normalizers.normalize_pose(amcl, make_odom())
    assert type(pose["localized"]) is bool
    # Must not raise "Object of type bool_ is not JSON serializable".
    assert json.loads(json.dumps(pose))["localized"] is True


def test_ws_client_json_default_coerces_numpy():
    np = pytest.importorskip("numpy")
    from patrolbot_web_bridge.ws_client import _json_default

    assert _json_default(np.bool_(True)) is True
    assert _json_default(np.float64(1.5)) == 1.5
    assert _json_default(np.array([1, 2])) == [1, 2]


def test_scan_mirror_reverses_angular_direction():
    """An upside-down laser (180deg roll) maps (r, theta) -> (r, -theta):
    negate angle_min and angle_increment, ranges keep their order."""
    scan = NS(angle_min=-math.pi / 2, angle_increment=0.01, range_min=0.05,
              range_max=8.0, ranges=[1.0, 2.0, 3.0, 4.0])
    plain = normalizers.normalize_scan(scan, max_points=360)
    mirrored = normalizers.normalize_scan(scan, max_points=360, mirror=True)
    assert mirrored["angle_min"] == -plain["angle_min"]
    assert mirrored["angle_increment"] == -plain["angle_increment"]
    assert mirrored["ranges"] == plain["ranges"]  # index order unchanged


def test_scan_angle_offset_shifts_start():
    scan = NS(angle_min=0.0, angle_increment=0.01, range_min=0.05,
              range_max=8.0, ranges=[1.0, 2.0])
    shifted = normalizers.normalize_scan(scan, max_points=360, angle_offset=1.0)
    assert shifted["angle_min"] == 1.0


def test_scan_decimation_and_no_returns():
    n = 720
    scan = NS(angle_min=-math.pi / 2, angle_increment=math.pi / n,
              range_min=0.05, range_max=8.0,
              ranges=[float("inf") if i % 7 == 0 else 2.0 for i in range(n)])
    result = normalizers.normalize_scan(scan, max_points=360)
    assert len(result["ranges"]) <= 360
    assert None in result["ranges"]
    assert 2.0 in result["ranges"]
    # Payload values are rounded to 6 decimals.
    assert abs(result["angle_increment"] - 2 * math.pi / n) < 1e-5


def test_path_keeps_goal_and_caps_points():
    poses = [NS(pose=NS(position=NS(x=float(i), y=0.0), orientation=quaternion(0)))
             for i in range(1000)]
    result = normalizers.normalize_path(NS(poses=poses), max_points=200)
    assert len(result["points"]) <= 201
    assert result["goal"]["x"] == 999.0
    assert result["points"][-1] == [999.0, 0.0]


def test_battery_nan_percentage_becomes_none():
    battery = NS(voltage=24.61, current=-1.53, percentage=float("nan"), power_supply_status=2)
    result = normalizers.normalize_battery(battery)
    assert result["percentage"] is None
    assert result["voltage"] == 24.61
    assert result["charging"] is False


def test_battery_fractional_percentage_scaled():
    battery = NS(voltage=25.0, current=2.0, percentage=0.85, power_supply_status=1)
    result = normalizers.normalize_battery(battery)
    assert result["percentage"] == 85.0
    assert result["charging"] is True


def test_base_state_charge_names_and_invalid_bumpers():
    state = NS(session_generation=4, link_connected=True, telemetry_age=0.12,
               hardware_state_valid=True, charge_state=3, charge_state_valid=True,
               motors_enabled=True, estop_pressed=False, flags=0, fault_flags=0,
               stall_value=0, bumpers_valid=False,
               front_bumper_pressed=True, rear_bumper_pressed=False)
    result = normalizers.normalize_base_state(state)
    assert result["charge_state"] == "float"
    # Unmapped bumpers must fail closed to "not pressed", not garbage.
    assert result["bumpers_front"] is False


def test_diagnostics_levels_and_dedupe():
    diag = NS(status=[
        NS(name="laser", level=b"\x01", message="slow"),
        NS(name="laser", level=b"\x00", message="dup ignored"),
        NS(name="base", level=2, message="fault"),
    ])
    result = normalizers.normalize_diagnostics(diag)
    assert result["items"][0] == {"name": "laser", "level": "WARN", "message": "slow"}
    assert result["items"][1]["level"] == "ERROR"
    assert len(result["items"]) == 2


def test_map_rle_matches_server_fixture_convention():
    fixture = json.loads((FIXTURES / "telemetry.map.json").read_text())["data"]
    # Rebuild the fixture's cells and re-encode: must round-trip identically.
    cells = []
    for value, count in fixture["rle"]:
        cells.extend([value] * count)
    grid = NS(
        info=NS(resolution=fixture["resolution"], width=fixture["width"],
                height=fixture["height"],
                origin=NS(position=NS(x=fixture["origin"]["x"], y=fixture["origin"]["y"]),
                          orientation=quaternion(fixture["origin"]["yaw"]))),
        data=cells,
    )
    result = normalizers.normalize_map(grid, fixture["map_version"], fixture["name"])
    decoded = []
    for value, count in result["rle"]:
        decoded.extend([value] * count)
    assert decoded == cells
    assert result["width"] == fixture["width"]
    assert result["origin"] == fixture["origin"]


def test_map_signature_detects_change():
    def grid(cells):
        return NS(info=NS(width=4, height=1), data=cells)

    same_a = normalizers.map_signature(grid([0, 0, 100, 0]))
    same_b = normalizers.map_signature(grid([0, 0, 100, 0]))
    different = normalizers.map_signature(grid([100, 100, 100, 0]))
    assert same_a == same_b
    assert same_a != different
