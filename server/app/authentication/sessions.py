"""Signed session cookies: base64(json).hmac — no server-side session store.

Deliberately dependency-free; the payload is small (user identity + expiry)
and integrity is what matters, not secrecy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

COOKIE_NAME = "patrolbot_session"


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def issue(secret: str, user: dict[str, Any], ttl_s: int) -> str:
    payload = dict(user, exp=int(time.time()) + ttl_s)
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"{raw}.{_sign(secret, raw.encode())}"


def verify(secret: str, token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    raw, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret, raw.encode()), signature):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload
