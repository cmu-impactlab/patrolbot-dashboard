"""Phased diagnostics and base-state simulation (port of POC publish_status)."""
from __future__ import annotations


def diagnostics_payload(elapsed: float, battery_level: float, bumper_active: bool) -> dict:
    warning_phase = elapsed % 30.0
    warning = 20.0 <= warning_phase < 27.0
    cycle = int(elapsed // 30) % 3
    items = [
        {
            "name": "base_controller",
            "level": "WARN" if warning and cycle == 0 else "OK",
            "message": "Simulated base-controller delay" if warning and cycle == 0 else "Base controller nominal",
        },
        {
            "name": "battery",
            "level": "WARN" if battery_level < 0.30 else "OK",
            "message": "Battery below 30%" if battery_level < 0.30 else "Battery cycle nominal",
        },
        {
            "name": "localization",
            "level": "WARN" if warning and cycle == 1 else "OK",
            "message": "Localization confidence reduced" if warning and cycle == 1 else "Localization available",
        },
        {
            "name": "navigation",
            "level": "WARN" if warning else "OK",
            "message": "Navigation temporarily degraded" if warning else "Patrol navigation active",
        },
        {
            "name": "bumpers",
            "level": "WARN" if bumper_active else "OK",
            "message": "Bumper pressed" if bumper_active else "All bumpers clear",
        },
    ]
    return {"items": items}


def base_state_payload(*, session_generation: int, charging: bool, docked: bool,
                       estop: bool, bumper_front: bool, bumper_rear: bool,
                       motors_enabled: bool = True,
                       undock_active: bool = False,
                       odom_epoch_valid: bool = False,
                       localization_recovery_required: bool = True,
                       localization_seed_stamp_ns: int = 0) -> dict:
    if charging:
        charge_state = "charging"
    elif docked:
        charge_state = "docked"
    else:
        charge_state = "not_charging"
    dock_state = (
        "DEPARTING" if undock_active
        else "DOCKED_CONFIRMED" if docked
        else "CLEAR_CONFIRMED"
    )
    return {
        "session_generation": session_generation,
        "odom_epoch_valid": odom_epoch_valid,
        "localization_recovery_required": localization_recovery_required,
        "localization_recovery_stage": (
            "wait-origin" if not odom_epoch_valid
            else "wait-operator-seed" if localization_recovery_required or localization_seed_stamp_ns <= 0
            else "ready"
        ),
        "localization_seed_stamp_ns": localization_seed_stamp_ns,
        "link_connected": True,
        "telemetry_age": 0.08,
        "hardware_state_valid": True,
        "charge_state": charge_state,
        "motors_enabled": motors_enabled and not estop,
        "estop_pressed": estop,
        "fault_flags": 0,
        "stall_value": 0,
        # The simulated drive base can always read its bumpers. Saying so
        # explicitly matters: undock requires a robot to confirm the readings
        # are good, not merely to stay silent about them.
        "bumpers_valid": True,
        "bumpers_front": bumper_front,
        "bumpers_rear": bumper_rear,
        "dock_state": dock_state,
        "dock_state_valid": True,
        "dock_phase": 1 if undock_active else 0,
        "dock_phase_name": "MOVING" if undock_active else "IDLE",
        "undock_active": undock_active,
        "undock_release_attempts": 0,
        "minimum_rear_range": 2.0,
        "rear_sonar_usable": True,
        "redock_inhibited": not docked,
        "redock_inhibit_remaining": 30.0 if not docked else 0.0,
        "undock_profile_commissioned": True,
    }
