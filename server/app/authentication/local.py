"""Single-local-user auth stub.

Phase 5 replaces this dependency with CMU OAuth; everything downstream
already works in terms of the resolved ``User``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str  # operator | observer | administrator


LOCAL_USER = User(id=1, username="local", display_name="Local Operator", role="administrator")


async def get_current_user() -> User:
    return LOCAL_USER
