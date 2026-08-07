"""Pure ROS-message → dashboard-payload normalizers.

Only attribute access on the message objects — no rclpy imports — so these
are unit-testable with SimpleNamespace stand-ins and no ROS graph.
"""
from __future__ import annotations

import math
from typing import Any

# ARIA / Pioneer chargeState convention (BaseState.charge_state, int16).
CHARGE_STATE_NAMES = {
    -1: "unknown",
    0: "not_charging",
    1: "bulk",
    2: "overcharge",
    3: "float",
}

DIAG_LEVEL_NAMES = {0: "OK", 1: "WARN", 2: "ERROR", 3: "STALE"}


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _finite(value: float, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def normalize_pose(amcl_pose: Any, odom: Any, covariance_warn: float = 0.25) -> dict:
    """Pose from AMCL when available (map frame), else odom; twist from odom.

    Degrades gracefully when Nav2 localization is down: an odom-only pose is
    marked localized=False so the UI can say the position is approximate.
    """
    if amcl_pose is not None:
        pose = amcl_pose.pose.pose
        covariance = list(amcl_pose.pose.covariance)
        # x, y and yaw variance terms of the 6x6 covariance. The covariance
        # array is numpy-backed, so coerce to native Python types — a numpy
        # bool_ (or float64) here would break json.dumps downstream.
        trace = float(covariance[0] + covariance[7] + covariance[35])
        localized = bool(trace < covariance_warn)
        frame = "map"
    elif odom is not None:
        pose = odom.pose.pose
        trace = None
        localized = False
        frame = "odom"
    else:
        return {}

    twist = odom.twist.twist if odom is not None else None
    q = pose.orientation
    return {
        "frame_id": frame,
        "x": round(pose.position.x, 3),
        "y": round(pose.position.y, 3),
        "yaw": round(quaternion_to_yaw(q.x, q.y, q.z, q.w), 4),
        "linear_velocity": round(twist.linear.x, 3) if twist else 0.0,
        "angular_velocity": round(twist.angular.z, 3) if twist else 0.0,
        "covariance_trace": round(trace, 4) if trace is not None else None,
        "localized": localized,
    }


def normalize_scan(scan: Any, max_points: int = 360,
                   angle_offset: float = 0.0, mirror: bool = False) -> dict:
    """Decimate the scan and express its angles in the robot's base frame.

    The dashboard projects points from the robot's base pose without /tf, so the
    laser mounting is applied here. `mirror` handles an upside-down laser (a
    180deg roll maps ray (r, theta) -> (r, -theta), i.e. reverse the angular
    direction); `angle_offset` (radians) handles a yaw-mounted laser. Ranges
    keep their index order — only the reported angles change.
    """
    ranges = list(scan.ranges)
    stride = max(1, math.ceil(len(ranges) / max_points))
    decimated = [_finite(r, 2) for r in ranges[::stride]]
    # Values outside the sensor's valid band are no-returns.
    lo, hi = scan.range_min, scan.range_max
    decimated = [r if r is not None and lo <= r <= hi else None for r in decimated]

    angle_min = scan.angle_min
    angle_increment = scan.angle_increment * stride
    if mirror:
        angle_min = -angle_min
        angle_increment = -angle_increment
    angle_min += angle_offset
    return {
        "angle_min": round(angle_min, 6),
        "angle_increment": round(angle_increment, 6),
        "ranges": decimated,
    }


def normalize_path(path: Any, max_points: int = 200) -> dict:
    poses = list(path.poses)
    stride = max(1, math.ceil(len(poses) / max_points))
    sampled = poses[::stride]
    if poses and (not sampled or sampled[-1] is not poses[-1]):
        sampled.append(poses[-1])
    points = [
        [round(p.pose.position.x, 3), round(p.pose.position.y, 3)] for p in sampled
    ]
    goal = None
    if poses:
        last = poses[-1].pose
        goal = {
            "x": round(last.position.x, 3),
            "y": round(last.position.y, 3),
            "yaw": round(
                quaternion_to_yaw(
                    last.orientation.x, last.orientation.y,
                    last.orientation.z, last.orientation.w,
                ),
                4,
            ),
        }
    return {"frame_id": "map", "points": points, "goal": goal}


def normalize_battery(battery: Any, charge_voltage_min: float | None = None) -> dict | None:
    """sensor_msgs/BatteryState → payload, or None when there is nothing to say.

    Returns None for a non-finite voltage. BatteryState publishers use NaN for
    "not measured", and voltage is the one field this payload cannot express as
    null — it is also the field the server's runtime estimator and the charge
    cross-check below both key off. Publishing NaN would put it in the JSON
    frame, where the browser's JSON.parse rejects the whole message. Dropping
    the sample is the honest option: the last good reading stays on screen and
    ages out through the normal freshness path.

    The PatrolBot firmware's state-of-charge is documented as not applicable
    for this model, so percentage is passed through only when finite — the
    server's voltage-trend estimator is the real source of remaining time.

    power_supply_status carries no validity flag (unlike BaseState, which has
    charge_state_valid) and this driver reports current as a constant 0.0, so
    the status bit is the only charge signal there is — and it lies. On
    2026-07-28, 38% of the samples it flagged as charging were below the
    charge voltage, including 81 consecutive samples pinned at exactly 25.9 V
    taken right after the robot had backed 0.60 m clear of the dock.

    So the claim is cross-checked against the one measurement that cannot be
    faked: a pack under charge sits at the charger's output voltage, well
    above its resting voltage. Below charge_voltage_min, "charging" is not
    believed. This only ever clears the flag — it never invents charging that
    the driver did not report.
    """
    voltage = getattr(battery, "voltage", None)
    if voltage is None or not math.isfinite(voltage):
        return None

    percentage = battery.percentage
    if percentage is not None and math.isfinite(percentage):
        # BatteryState convention is 0..1; tolerate 0..100 publishers.
        percentage = round(percentage * 100.0, 1) if percentage <= 1.0 else round(percentage, 1)
    else:
        percentage = None
    current = _finite(battery.current, 2)
    # POWER_SUPPLY_STATUS_CHARGING == 1
    charging = getattr(battery, "power_supply_status", 0) == 1
    if charging and charge_voltage_min is not None and voltage < charge_voltage_min:
        charging = False
    return {
        "voltage": round(voltage, 2),
        "current": current,
        "percentage": percentage,
        "charging": charging,
    }


def normalize_base_state(state: Any) -> dict:
    charge = CHARGE_STATE_NAMES.get(int(state.charge_state), f"state_{int(state.charge_state)}")
    if not getattr(state, "charge_state_valid", True):
        charge = "unknown"
    bumpers_valid = getattr(state, "bumpers_valid", True)
    # The SBC-owned dock observer is authoritative for physical dock contact.
    # charge_state is only one noisy input to it and can re-latch after the
    # robot has proved that it is clear. Keep both signals in the dashboard
    # payload so the UI never turns a stale charger level back into "docked".
    dock_state_valid = bool(getattr(state, "dock_state_valid", False))
    dock_state = str(getattr(state, "dock_state", "UNKNOWN")).strip().upper()
    if not dock_state_valid:
        dock_state = "UNKNOWN"
    return {
        "session_generation": int(state.session_generation),
        "link_connected": bool(state.link_connected),
        "telemetry_age": round(float(state.telemetry_age), 3),
        "hardware_state_valid": bool(state.hardware_state_valid),
        "charge_state": charge,
        "motors_enabled": bool(state.motors_enabled),
        "estop_pressed": bool(state.estop_pressed),
        "fault_flags": int(state.fault_flags),
        "stall_value": int(state.stall_value),
        "bumpers_front": bool(state.front_bumper_pressed) if bumpers_valid else False,
        "bumpers_rear": bool(state.rear_bumper_pressed) if bumpers_valid else False,
        "dock_state": dock_state,
        "dock_state_valid": dock_state_valid,
        "dock_phase": int(getattr(state, "dock_phase", 0)),
        "dock_phase_name": str(getattr(state, "dock_phase_name", "IDLE")),
        "undock_active": bool(getattr(state, "undock_active", False)),
        "undock_release_attempts": int(getattr(state, "undock_release_attempts", 0)),
        "minimum_rear_range": _finite(
            getattr(state, "minimum_rear_range", None), 3),
        "rear_sonar_usable": bool(getattr(state, "rear_sonar_usable", False)),
        "redock_inhibited": bool(getattr(state, "redock_inhibited", False)),
        "redock_inhibit_remaining": round(
            float(getattr(state, "redock_inhibit_remaining", 0.0)), 1),
        "undock_profile_commissioned": bool(
            getattr(state, "undock_profile_commissioned", False)),
    }


def normalize_diagnostics(diag_array: Any) -> dict:
    items = []
    seen: set[str] = set()
    for status in diag_array.status:
        name = status.name
        if name in seen:
            continue
        seen.add(name)
        level = status.level
        if isinstance(level, bytes):
            level = level[0] if level else 3
        items.append({
            "name": name,
            "level": DIAG_LEVEL_NAMES.get(int(level), "STALE"),
            "message": status.message,
        })
    return {"items": items}


def encode_rle(cells: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for value in cells:
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([int(value), 1])
    return runs


def normalize_map(grid: Any, map_version: int, name: str) -> dict:
    q = grid.info.origin.orientation
    return {
        "map_version": map_version,
        "name": name,
        "resolution": round(grid.info.resolution, 4),
        "width": int(grid.info.width),
        "height": int(grid.info.height),
        "origin": {
            "x": round(grid.info.origin.position.x, 3),
            "y": round(grid.info.origin.position.y, 3),
            "yaw": round(quaternion_to_yaw(q.x, q.y, q.z, q.w), 4),
        },
        "rle": encode_rle(list(grid.data)),
    }


def map_signature(grid: Any) -> tuple:
    """Cheap change detector so unchanged maps are not re-sent."""
    data = grid.data
    return (
        int(grid.info.width),
        int(grid.info.height),
        len(data),
        int(sum(data[:: max(1, len(data) // 512)])) if len(data) else 0,
    )
