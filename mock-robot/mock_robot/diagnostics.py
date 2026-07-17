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
                       motors_enabled: bool = True) -> dict:
    if charging:
        charge_state = "charging"
    elif docked:
        charge_state = "docked"
    else:
        charge_state = "not_charging"
    return {
        "session_generation": session_generation,
        "link_connected": True,
        "telemetry_age": 0.08,
        "hardware_state_valid": True,
        "charge_state": charge_state,
        "motors_enabled": motors_enabled and not estop,
        "estop_pressed": estop,
        "fault_flags": 0,
        "stall_value": 0,
        "bumpers_front": bumper_front,
        "bumpers_rear": bumper_rear,
    }
