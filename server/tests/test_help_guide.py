"""Account-wide, versioned help-guide onboarding persistence and API."""

import sqlite3

from fastapi.testclient import TestClient

from app.authentication.sessions import COOKIE_NAME, issue
from app.database.repo import Database
from app.help_guide import HELP_GUIDE_VERSION
from app.main import create_app
from app.settings import Settings


def _legacy_users_database(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE users ("
        "id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
        "display_name TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    connection.execute(
        "INSERT INTO users (id, username, display_name) "
        "VALUES (7, 'existing', 'Existing User')"
    )
    connection.commit()
    connection.close()


async def test_old_schema_migrates_existing_user_to_unseen(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_users_database(path)

    database = Database(path)
    await database.init()
    try:
        async with database.db.execute("PRAGMA table_info(users)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        assert "help_guide_version_seen" in columns
        assert await database.get_help_guide_version_seen(7) == 0
    finally:
        await database.close()


async def test_new_users_start_unseen_and_accounts_are_isolated(tmp_path):
    database = Database(str(tmp_path / "accounts.db"))
    await database.init()
    try:
        first = await database.get_or_create_user("first", "First")
        second = await database.get_or_create_user("second", "Second")
        assert await database.get_help_guide_version_seen(first) == 0
        assert await database.get_help_guide_version_seen(second) == 0

        assert await database.mark_help_guide_seen(first, HELP_GUIDE_VERSION) == 1
        assert await database.get_help_guide_version_seen(second) == 0
        # A stale client cannot roll a newer acknowledgement backward.
        assert await database.mark_help_guide_seen(first, 0) == 1
    finally:
        await database.close()


async def test_help_guide_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "twice.db")
    _legacy_users_database(path)
    for _ in range(3):
        database = Database(path)
        await database.init()
        try:
            assert await database.get_help_guide_version_seen(7) == 0
        finally:
            await database.close()


def test_local_authenticated_get_and_put(tmp_path):
    app = create_app(Settings(database_path=str(tmp_path / "api.db")))
    with TestClient(app) as client:
        unseen = client.get("/api/help-guide/status")
        assert unseen.status_code == 200
        assert unseen.json() == {
            "current_version": 1,
            "seen_version": 0,
            "should_prompt": True,
        }

        seen = client.put("/api/help-guide/status")
        assert seen.status_code == 200
        assert seen.json() == {
            "current_version": 1,
            "seen_version": 1,
            "should_prompt": False,
        }
        assert client.put("/api/help-guide/status").json() == seen.json()


def test_oidc_api_requires_a_session_and_uses_its_account(tmp_path):
    settings = Settings(
        database_path=str(tmp_path / "oidc.db"),
        auth_mode="oidc",
        session_secret="guide-secret",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/help-guide/status").status_code == 401
        assert client.put("/api/help-guide/status").status_code == 401

        token = issue("guide-secret", {
            "id": 1,
            "username": "local",
            "display_name": "Local Operator",
            "role": "administrator",
        }, 60)
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/api/help-guide/status").json()["should_prompt"] is True
        assert client.put("/api/help-guide/status").json()["should_prompt"] is False
