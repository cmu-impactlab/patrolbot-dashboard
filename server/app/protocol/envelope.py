from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

PROTOCOL_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Envelope(BaseModel):
    version: Literal[1]
    type: str
    robot_id: str
    sequence: int
    timestamp: str
    data: dict[str, Any]


class ProtocolError(ValueError):
    pass


def decode(raw: str | bytes) -> tuple[Envelope, Any]:
    """Parse a frame into (envelope, typed payload).

    Unknown types raise ProtocolError; payloads failing their model raise too.
    """
    from .messages import TYPE_REGISTRY

    try:
        envelope = Envelope.model_validate_json(raw)
    except Exception as exc:  # pydantic ValidationError or JSON error
        raise ProtocolError(f"invalid envelope: {exc}") from exc
    model = TYPE_REGISTRY.get(envelope.type)
    if model is None:
        raise ProtocolError(f"unknown message type: {envelope.type}")
    try:
        payload = model.model_validate(envelope.data)
    except Exception as exc:
        raise ProtocolError(f"invalid {envelope.type} payload: {exc}") from exc
    return envelope, payload


def encode(type_: str, robot_id: str, sequence: int, data: Any, timestamp: str | None = None) -> str:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    return json.dumps(
        {
            "version": PROTOCOL_VERSION,
            "type": type_,
            "robot_id": robot_id,
            "sequence": sequence,
            "timestamp": timestamp or utc_now(),
            "data": data,
        },
        separators=(",", ":"),
    )
