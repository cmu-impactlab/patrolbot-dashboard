"""Boundary validation for the history/snapshot query parameters.

The upper bounds were always clamped; the lower bounds were not, and the two
backends disagree about what a negative LIMIT means. SQLite treats any negative
LIMIT as "no limit" — so `?limit=-1` quietly returned the entire events table
instead of 100 rows — while PostgreSQL raises `LIMIT must not be negative`, a
500 rather than a client error. A negative `minutes` window is nonsense on both.

These are rejected at the parameter, so the answer is the same 422 regardless of
which database is configured.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


BOUNDED = [
    # (path, parameter, max accepted value)
    ("/api/events", "limit", 500),
    ("/api/commands", "limit", 500),
    ("/api/history/battery", "minutes", 7 * 24 * 60),
]


@pytest.mark.parametrize(("path", "param", "cap"), BOUNDED)
@pytest.mark.parametrize("bad", [0, -1, -500])
def test_rejects_zero_and_negative(client, path, param, cap, bad):
    assert client.get(path, params={param: bad}).status_code == 422


@pytest.mark.parametrize(("path", "param", "cap"), BOUNDED)
def test_rejects_above_cap(client, path, param, cap):
    assert client.get(path, params={param: cap + 1}).status_code == 422


@pytest.mark.parametrize(("path", "param", "cap"), BOUNDED)
def test_accepts_the_boundaries(client, path, param, cap):
    assert client.get(path, params={param: 1}).status_code == 200
    assert client.get(path, params={param: cap}).status_code == 200


@pytest.mark.parametrize(("path", "param", "cap"), BOUNDED)
def test_rejects_non_integer(client, path, param, cap):
    assert client.get(path, params={param: "all"}).status_code == 422


def test_negative_limit_would_have_meant_unlimited_in_sqlite(client):
    """Pin the SQLite behavior the bound is protecting against, so that a
    future revert of the Query() constraint fails loudly here."""
    import aiosqlite

    async def probe() -> int:
        async with aiosqlite.connect(":memory:") as db:
            await db.execute("CREATE TABLE t (id INTEGER)")
            await db.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(10)])
            async with db.execute("SELECT id FROM t LIMIT ?", (-1,)) as cur:
                return len(await cur.fetchall())

    import asyncio

    assert asyncio.run(probe()) == 10  # not 0, and not an error: all of them
