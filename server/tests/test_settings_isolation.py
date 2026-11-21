"""Fresh processes exercise real import order without launching servers."""
import ast
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ["database_url", "DATABASE_URL", "PATROLBOT_DATABASE_URL",
         "DaTaBaSe_Url", "PaTrOlBoT_DaTaBaSe_Url"]
URL = "postgresql://isolation-user:isolation-secret@unused/db"


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("source", ["environment", "dotenv", "both"])
def test_fresh_process_isolation(tmp_path, name, source):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PATROLBOT_") and key.upper() != "DATABASE_URL"}
    env["PYTHONPATH"] = str(ROOT / "server")
    env["PATROLBOT_DATABASE_PATH"] = str(tmp_path / "must-not-create.db")
    env["patrolbot_database_path"] = str(tmp_path / "also-must-not-create.db")
    if source in {"environment", "both"}:
        env[name] = URL
    if source in {"dotenv", "both"}:
        (tmp_path / ".env").write_text(f'{name}="{URL}"\n')

    # Importing settings is safe; actual application startup still refuses URLs.
    code = '''
import runpy, sys
from app.settings import Settings
try:
    import app.main
except RuntimeError as error:
    assert "obsolete" in str(error)
    assert "isolation-secret" not in repr(error)
else:
    raise AssertionError("startup accepted obsolete URL")
runpy.run_path(sys.argv[1])
assert Settings().database_url == ""
'''
    result = subprocess.run([sys.executable, "-B", "-c", code,
                             str(ROOT / "server/tests/conftest.py")],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "isolation-secret" not in result.stdout + result.stderr
    assert not (tmp_path / "must-not-create.db").exists()

    # Evaluate the actual launcher environment and backend command, stubbing only
    # uvicorn.run so this test cannot open a listening socket or database.
    tree = ast.parse((ROOT / "frontend/integration/serve_mock.py").read_text())
    assignment, = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "env"
                           for target in node.targets)]
    scope = {"os": SimpleNamespace(environ=env), "directory": str(tmp_path),
             "PYTHON": sys.executable, "PORT": 8127, "args": SimpleNamespace(host="127.0.0.1")}
    child_env = eval(compile(ast.Expression(assignment.value), "launcher", "eval"), scope)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"]
    assert len(calls) == 2
    assert all(any(k.arg == "env" and isinstance(k.value, ast.Name)
                   and k.value.id == "env" for k in call.keywords) for call in calls)
    command = eval(compile(ast.Expression(calls[0].args[0]), "launcher", "eval"), scope)
    command[2] = (
        "import sys, types, importlib; "
        "sys.modules['uvicorn'] = types.SimpleNamespace(run=lambda *a, **k: "
        "importlib.import_module('app.main')); " + command[2]
        + "; assert Settings().database_path == sys.argv[3]"
    )
    command.append(str(tmp_path / "test.db"))
    result = subprocess.run(command, cwd=tmp_path, env=child_env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "isolation-secret" not in result.stdout + result.stderr
    assert not (tmp_path / "test.db").exists()
    assert not (tmp_path / "also-must-not-create.db").exists()


def test_non_database_source_precedence_unchanged(tmp_path, monkeypatch):
    from app.settings import Settings
    dotenv = tmp_path / ".env"
    dotenv.write_text("PATROLBOT_ROBOT_TOKEN=dotenv-token\nPATROLBOT_EVENT_LIMIT=7\n")
    monkeypatch.setenv("PATROLBOT_ROBOT_TOKEN", "environment-token")
    assert Settings(_env_file=dotenv).robot_token == "environment-token"
    settings = Settings(_env_file=dotenv, robot_token="constructor-token")
    assert settings.robot_token == "constructor-token"
    assert settings.event_limit == 7
