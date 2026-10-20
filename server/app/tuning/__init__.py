"""Robot tuning: validation, authorization and the robot WebSocket broker.

Slice-level status: the runtime capability is intentionally fail-closed. The
server can read a robot snapshot and forward admin-authorized canonical
requests, but the robot bridge advertises ``robot_tuning_read_v1`` only when the
core ``/patrolbot/tuning`` service is actually reachable and does not advertise
``robot_tuning_v1`` for mutations until safe activation support is proven.
"""
from __future__ import annotations

from .broker import TuningBroker, TuningResponse

__all__ = ["TuningBroker", "TuningResponse"]
