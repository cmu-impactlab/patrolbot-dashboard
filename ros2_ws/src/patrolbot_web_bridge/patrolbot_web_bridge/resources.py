"""Raspberry Pi resource sampling (psutil + sysfs)."""
from __future__ import annotations

import os


def sample() -> dict:
    try:
        import psutil
    except ImportError:
        return {"cpu_percent": 0.0, "memory_percent": 0.0, "cpu_temp_c": None,
                "disk_percent": 0.0, "wifi_signal_dbm": None}

    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "cpu_temp_c": _cpu_temp(psutil),
        "disk_percent": psutil.disk_usage("/").percent,
        "wifi_signal_dbm": _wifi_dbm(),
    }


def _cpu_temp(psutil_module) -> float | None:
    try:
        temps = psutil_module.sensors_temperatures()
        for key in ("cpu_thermal", "coretemp", "soc_thermal"):
            if key in temps and temps[key]:
                return round(temps[key][0].current, 1)
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except Exception:
        pass
    # RPi fallback
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except OSError:
        return None


def _wifi_dbm() -> int | None:
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
        for line in lines[2:]:
            parts = line.split()
            if len(parts) >= 4:
                return int(float(parts[3].rstrip(".")))
    except (OSError, ValueError):
        pass
    return None


def is_raspberry_pi() -> bool:
    try:
        with open("/proc/device-tree/model") as f:
            return "raspberry" in f.read().lower()
    except OSError:
        return os.uname().machine.startswith("aarch64")
