"""Event ids must be unique across robot sessions, and the schema migration
that enforces it must never cost an existing deployment its event log.

Every RobotState used to own a counter, and every counter was seeded from the
same persisted value, so a second robot re-issued ids the first had already
used. Those ids are the events table's primary key and the identity the browser
tracks "seen" against: the collisions overwrote persisted rows and marked one
robot's alerts read because another robot's same-numbered alert had been
acknowledged.
"""
import sqlite3

import pytest

from app.database.repo import Database
from app.protocol.messages import EventData
from app.settings import Settings
from app.telemetry.hub import TelemetryHub
from app.telemetry.store import EventIdAllocator, RobotState


# -- allocation -------------------------------------------------------------

def test_two_sessions_never_share_an_id():
    hub = TelemetryHub(Settings())
    first = hub.get_or_create_state("patrolbot-01").state
    second = hub.get_or_create_state("patrolbot-02").state

    ids = []
    for _ in range(50):
        ids.append(first.add_event("info", "a", "m").id)
        ids.append(second.add_event("info", "b", "m").id)

    assert len(set(ids)) == len(ids)
    assert sorted(ids) == list(range(1, 101))


def test_a_session_created_later_continues_from_the_shared_counter():
    """The original shape of the bug: the second robot connects after the
    first has been running, and starts again from the same seed."""
    hub = TelemetryHub(Settings())
    first = hub.get_or_create_state("patrolbot-01").state
    for _ in range(10):
        first.add_event("info", "a", "m")

    second = hub.get_or_create_state("patrolbot-02").state
    assert second.add_event("info", "b", "m").id == 11


def test_seeding_from_the_event_log_never_moves_backwards():
    """A second seed (or a re-seed after ids were handed out) must not
    re-issue numbers already used."""
    allocator = EventIdAllocator()
    allocator.seed(500)
    assert allocator.allocate() == 500

    allocator.seed(100)  # a stale seed
    assert allocator.allocate() == 501


def test_hub_seed_reaches_sessions_created_before_and_after():
    hub = TelemetryHub(Settings())
    early = hub.get_or_create_state("patrolbot-01").state
    hub.set_event_seed(900)
    late = hub.get_or_create_state("patrolbot-02").state

    assert early.add_event("info", "a", "m").id == 900
    assert late.add_event("info", "b", "m").id == 901


def test_a_standalone_state_still_allocates_on_its_own():
    """The empty snapshot served before any robot connects builds a bare
    RobotState; it must not need a hub to work."""
    state = RobotState(Settings(), "patrolbot-01")
    assert state.add_event("info", "a", "m").id == 1
    assert state.add_event("info", "a", "m").id == 2


# -- persistence ------------------------------------------------------------

@pytest.fixture()
async def db(tmp_path):
    database = Database(str(tmp_path / "e.db"))
    await database.init()
    yield database
    await database.close()


def event(id_: int, title: str = "t") -> EventData:
    return EventData(id=id_, ts="2026-08-08T00:00:00Z", severity="info",
                     title=title, message="m")


async def test_two_robots_keep_their_own_rows(db):
    """Even given colliding ids — which the allocator now prevents — the
    composite key means one robot cannot overwrite the other's history."""
    await db.add_event("patrolbot-01", event(1, "first robot"))
    await db.add_event("patrolbot-02", event(1, "second robot"))

    rows = await db.get_events(limit=50)
    assert len(rows) == 2
    assert {(row["robot_id"], row["title"]) for row in rows} == {
        ("patrolbot-01", "first robot"), ("patrolbot-02", "second robot")}


async def test_replaying_the_same_event_still_updates_in_place(db):
    await db.add_event("patrolbot-01", event(1, "before"))
    await db.add_event("patrolbot-01", event(1, "after"))

    rows = await db.get_events(limit=50)
    assert len(rows) == 1 and rows[0]["title"] == "after"


async def test_retention_does_not_delete_the_other_robots_events(db):
    """Retention used to be "id not among the newest 5000 ids", which deleted
    a second robot's rows whenever their ids fell outside that window."""
    for id_ in range(1, 30):
        await db.add_event("patrolbot-01", event(id_))
    await db.add_event("patrolbot-02", event(1000))

    rows = await db.get_events(limit=100)
    assert any(row["robot_id"] == "patrolbot-02" for row in rows)
    assert len([r for r in rows if r["robot_id"] == "patrolbot-01"]) == 29


async def test_next_event_id_continues_past_every_robot(db):
    await db.add_event("patrolbot-01", event(7))
    await db.add_event("patrolbot-02", event(12))
    assert await db.next_event_id() == 13


# -- the migration, against a real pre-migration database -------------------

def make_legacy_database(path: str, rows: list[tuple[int, str, str]]) -> None:
    """Build an events table exactly as it was before this change."""
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE events (
        id INTEGER PRIMARY KEY, robot_id TEXT NOT NULL, ts TEXT NOT NULL,
        severity TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL)""")
    conn.executemany(
        "INSERT INTO events (id, robot_id, ts, severity, title, message) "
        "VALUES (?,?,?,'info',?,'m')",
        [(id_, robot_id, "2026-08-08T00:00:00Z", title) for id_, robot_id, title in rows])
    conn.commit()
    conn.close()


def primary_key_of(path: str, table: str = "events") -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[5]}
    finally:
        conn.close()


async def test_migration_preserves_every_existing_row(tmp_path):
    path = str(tmp_path / "legacy.db")
    rows = [(id_, "patrolbot-01", f"event {id_}") for id_ in range(1, 41)]
    make_legacy_database(path, rows)
    assert primary_key_of(path) == {"id"}

    database = Database(path)
    await database.init()
    migrated = await database.get_events(limit=100)
    await database.close()

    assert primary_key_of(path) == {"robot_id", "id"}
    assert len(migrated) == 40
    assert {row["title"] for row in migrated} == {f"event {id_}" for id_ in range(1, 41)}


async def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "twice.db")
    make_legacy_database(path, [(1, "patrolbot-01", "only")])

    for _ in range(3):
        database = Database(path)
        await database.init()
        rows = await database.get_events(limit=10)
        await database.close()
        assert len(rows) == 1 and rows[0]["title"] == "only"


async def test_the_events_index_survives_the_migration(tmp_path):
    path = str(tmp_path / "indexed.db")
    make_legacy_database(path, [(1, "patrolbot-01", "x")])

    database = Database(path)
    await database.init()
    await database.close()

    conn = sqlite3.connect(path)
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)")}
    conn.close()
    assert "idx_events_ts" in indexes


async def test_a_failed_migration_leaves_the_original_table_intact(tmp_path, monkeypatch):
    """The property that matters for a live deployment: if anything goes wrong
    the events log is exactly as it was, and the server still starts."""
    path = str(tmp_path / "fragile.db")
    rows = [(id_, "patrolbot-01", f"event {id_}") for id_ in range(1, 11)]
    make_legacy_database(path, rows)

    real_execute = Database._migrate_events_primary_key

    async def failing(self):
        # Fail after the copy but before the swap, the worst moment.
        original = self._db.execute

        # Not `async def`: aiosqlite's execute() returns an object that is both
        # awaited and used as an async context manager, so the wrapper has to
        # hand that same object back untouched.
        def execute(sql, *args, **kwargs):
            if sql.strip().startswith("DROP TABLE events"):
                raise sqlite3.OperationalError("simulated failure mid-migration")
            return original(sql, *args, **kwargs)

        monkeypatch.setattr(self._db, "execute", execute)
        return await real_execute(self)

    monkeypatch.setattr(Database, "_migrate_events_primary_key", failing)

    database = Database(path)
    await database.init()  # must not raise
    surviving = await database.get_events(limit=100)
    await database.close()

    assert len(surviving) == 10
    assert primary_key_of(path) == {"id"}  # untouched, still usable
