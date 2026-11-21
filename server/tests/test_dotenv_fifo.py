"""Unsupported dotenv streams must be rejected before any blocking read."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.settings import Settings


@pytest.mark.parametrize("kind", ["named", "tilde", "pipe"])
@pytest.mark.parametrize("name", ["", "PATROLBOT_DATABASE_URL", "DATABASE_URL"])
@pytest.mark.parametrize("selection", ["constructor", "config", "sentinel"])
@pytest.mark.parametrize("position", ["single", "first", "last"])
def test_fifo_dotenv_refused_without_consuming(tmp_path, kind, name, selection, position):
    # A child with a live writer bounds failures that would otherwise block pytest.
    code = r'''
import os
from pathlib import Path
import sys
import traceback
from app.main import create_app
from app.settings import Settings

kind, name, selection, position = sys.argv[1:]
payload = (f'{name}=postgresql://user:fifo-credential-marker@unused/db\n'
           f'{name.swapcase()}=""\n').encode() if name else b''
if kind == 'pipe':
    reader, writer = os.pipe()
    path = Path(f'/proc/self/fd/{reader}')
else:
    path = Path.cwd() / 'sensitive-filename.env'
    os.mkfifo(path)
    reader = writer = os.open(path, os.O_RDWR | os.O_NONBLOCK)
assert path.is_fifo()
if payload:
    os.write(writer, payload)
if kind == 'tilde':
    os.environ['HOME'] = str(Path.cwd())
    path = '~/sensitive-filename.env'
regular = Path('regular.env')
regular.write_text('PATROLBOT_ROBOT_TOKEN=regular-token\n')
paths = {'single': path, 'first': (path, regular), 'last': (regular, path)}[position]
options = {'database_url': '', 'database_path': 'must-not-create.db'}
if selection == 'constructor':
    options['_env_file'] = paths
else:
    Settings.model_config['env_file'] = paths
    if selection == 'sentinel':
        options['_env_file'] = Path('')
try:
    create_app(Settings(**options))
except RuntimeError as error:
    assert str(error) == 'FIFO dotenv inputs are unsupported; use regular dotenv files.'
    rendered = ''.join(traceback.format_exception(error))
    assert 'fifo-credential-marker' not in rendered
    assert 'sensitive-filename' not in rendered
    assert str(Path.cwd()) not in str(error)
    assert str(path) not in str(error)
else:
    raise AssertionError('FIFO dotenv input accepted')
os.set_blocking(reader, False)
if payload:
    assert os.read(reader, len(payload) + 1) == payload
try:
    os.read(reader, 1)
except BlockingIOError:
    pass
else:
    raise AssertionError('expected an empty stream with its writer still open')
assert not Path('must-not-create.db').exists()
assert not Path('data/patrolbot.db').exists()
os.close(reader)
if writer != reader:
    os.close(writer)
'''
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PATROLBOT_") and key.upper() != "DATABASE_URL"}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, kind, name, selection, position],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "must-not-create.db").exists()


@pytest.mark.parametrize("override", [None, "regular", "missing", "directory", "empty-list"])
def test_explicit_override_ignores_configured_fifo(tmp_path, monkeypatch, override):
    fifo = tmp_path / "unused.env"
    os.mkfifo(fifo)
    monkeypatch.setitem(Settings.model_config, "env_file", fifo)
    regular = tmp_path / "regular.env"
    regular.write_text("PATROLBOT_ROBOT_TOKEN=dotenv-token\nPATROLBOT_EVENT_LIMIT=7\n")
    paths = {None: None, "regular": regular, "missing": tmp_path / "missing.env",
             "directory": tmp_path, "empty-list": []}
    settings = Settings(_env_file=paths[override])
    assert settings.robot_token == ("dotenv-token" if override == "regular" else "dev-token")
    assert settings.event_limit == (7 if override == "regular" else 5000)


@pytest.mark.parametrize("selection", ["omitted", "sentinel"])
def test_regular_configured_default_and_sentinel(tmp_path, monkeypatch, selection):
    regular = tmp_path / "regular.env"
    regular.write_text("PATROLBOT_ROBOT_TOKEN=dotenv-token\n")
    monkeypatch.setitem(Settings.model_config, "env_file", regular)
    options = {} if selection == "omitted" else {"_env_file": Path("")}
    assert Settings(**options).robot_token == "dotenv-token"


def test_default_dotenv_and_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    assert Settings().robot_token == "dev-token"
    (tmp_path / ".env").write_text("PATROLBOT_ROBOT_TOKEN=default-dotenv-token\n")
    assert Settings().robot_token == "default-dotenv-token"
    assert Settings(_env_file=None).robot_token == "dev-token"
