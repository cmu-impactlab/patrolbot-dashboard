"""SQLite-only configuration must never silently abandon a configured database."""
import builtins

import pytest
from fastapi.testclient import TestClient

from app.database import create_database
from app.database.repo import Database
from app.main import create_app
from app.settings import Settings


@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("source", ["argument", "environment", "dotenv"])
@pytest.mark.parametrize("name", ["database_url", "DATABASE_URL", "PATROLBOT_DATABASE_URL",
                                  "PaTrOlBoT_DaTaBaSe_Url", "DaTaBaSe_Url"])
@pytest.mark.parametrize("url", [
    "postgres://user:secret@unused/db",
    "postgresql://user:secret@unused/db",
    "sqlite:///other.db", "mysql://unused/db", "unknown", " ",
])
def test_nonempty_database_url_refused(tmp_path, monkeypatch, environment, source, name, url):
    path = tmp_path / "must-not-create.db"
    options = dict(environment=environment, database_path=str(path))
    if source == "argument":
        options[name] = url
    elif source == "environment":
        monkeypatch.setenv(name, url)
    else:
        dotenv = tmp_path / ".env"
        dotenv.write_text(f'{name}="{url}"\n')
        options["_env_file"] = dotenv
    with pytest.raises(RuntimeError) as error:
        create_app(Settings(**options))
    assert str(error.value) == (
        "Database URL configuration is obsolete. SQLite only; use "
        "PATROLBOT_DATABASE_PATH; migrate data explicitly."
    )
    assert "secret" not in repr(error.value)
    assert not path.exists()


@pytest.mark.parametrize("value", [123, ["secret"], {"password": "secret"}])
@pytest.mark.parametrize("name", ["database_url", "DATABASE_URL", "PATROLBOT_DATABASE_URL"])
def test_invalid_constructor_url_is_redacted(name, value):
    with pytest.raises(RuntimeError) as error:
        Settings(**{name: value})
    assert "secret" not in repr(error.value)


@pytest.mark.parametrize("source", ["argument", "environment", "dotenv"])
def test_empty_canonical_cannot_hide_alias(tmp_path, monkeypatch, source):
    options = {"database_url": ""}
    if source == "argument":
        options["DATABASE_URL"] = "secret"
    elif source == "environment":
        monkeypatch.setenv("PATROLBOT_DATABASE_URL", "")
        monkeypatch.setenv("DATABASE_URL", "secret")
    else:
        path = tmp_path / ".env"
        path.write_text('PATROLBOT_DATABASE_URL=""\nDATABASE_URL=secret\n')
        options["_env_file"] = path
    with pytest.raises(RuntimeError, match="obsolete"):
        Settings(**options)


@pytest.mark.parametrize("name", ["database_url", "DATABASE_URL", "PATROLBOT_DATABASE_URL",
                                  "DaTaBaSe_Url", "PaTrOlBoT_DaTaBaSe_Url"])
@pytest.mark.parametrize("source", ["argument", "environment", "dotenv"])
def test_empty_alias_is_allowed(tmp_path, monkeypatch, name, source):
    options = {}
    if source == "argument":
        options[name] = ""
    elif source == "environment":
        monkeypatch.setenv(name, "")
    else:
        path = tmp_path / ".env"
        path.write_text(f'{name}=""\n')
        options["_env_file"] = path
    assert Settings(**options).database_url == ""


@pytest.mark.parametrize("name", ["DATABASE_URL", "PATROLBOT_DATABASE_URL"])
@pytest.mark.parametrize("source", ["environment", "dotenv", "dotenv-files"])
def test_empty_case_variant_cannot_hide_url(tmp_path, monkeypatch, name, source):
    options = {}
    if source == "environment":
        monkeypatch.setenv(name, "secret")
        monkeypatch.setenv(name.lower(), "")
    else:
        first, second = tmp_path / "first.env", tmp_path / "second.env"
        if source == "dotenv":
            first.write_text(f'{name}=secret\n{name.lower()}=""\n')
            options["_env_file"] = first
        else:
            first.write_text(f'{name}=secret\n')
            second.write_text(f'{name}=""\n')
            options["_env_file"] = (first, second)
    with pytest.raises(RuntimeError, match="obsolete"):
        Settings(**options)


@pytest.mark.parametrize("source", ["omitted", "argument", "environment", "dotenv"])
def test_sqlite_factory_without_optional_driver(tmp_path, monkeypatch, source):
    original_import = builtins.__import__

    def no_postgres(name, *args, **kwargs):
        assert "asyncpg" not in name and name != "app.database.pg"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_postgres)
    path = tmp_path / "configured.db"
    options = dict(database_path=str(path), event_limit=12, battery_history_days=4)
    if source == "argument":
        options["database_url"] = ""
    elif source == "environment":
        monkeypatch.setenv("PATROLBOT_DATABASE_URL", "")
    elif source == "dotenv":
        dotenv = tmp_path / ".env"
        dotenv.write_text('PATROLBOT_DATABASE_URL=""\n')
        options["_env_file"] = dotenv
    settings = Settings(**options)
    db = create_database(settings)
    assert type(db) is Database
    assert (db.path, db.event_limit, db.battery_history_days) == (str(path), 12, 4)
    with TestClient(create_app(settings)) as client:
        assert type(client.app.state.db) is Database
        assert client.get("/api/health").status_code == 200
    assert path.is_file()


def test_default_database_path_stays_compatible():
    assert create_database(Settings()).path == "data/patrolbot.db"
