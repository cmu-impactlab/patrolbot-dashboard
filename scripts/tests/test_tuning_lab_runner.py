"""Wrapper-level tests for infrastructure/tuning-lab/run_host.sh.

No real Docker, no network, no ROS: a fake ``docker`` executable is placed first
on ``PATH`` for the duration of each run. It logs every argv and answers the
image-inspect / tag / build / run calls the wrapper makes, so the tests can pin
the exact command lines the host wrapper emits.

Regression focus: the base image must be built from a named, content-addressed
local tag. Passing a bare ``sha256:<id>`` as the ``FROM`` argument makes
BuildKit resolve it as the Docker Hub repo ``sha256`` and fail; the wrapper must
instead tag the cached base by content ID, verify the tag still resolves to that
exact ID, and only then build offline.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_HOST = REPO_ROOT / "infrastructure" / "tuning-lab" / "run_host.sh"

BASE_REF = "patrolbot:joystick-handshake-diagnostic-20261015-r3"
BASE_ID = "sha256:" + "4" * 64
BASE_TAG = "patrolbot-tuning-base:" + "4" * 64
LAB_REF = "patrolbot-dashboard:tuning-lab"
LAB_ID = "sha256:" + "7" * 64

# A full name:tag reference (like the wrapper's content tag), not a bare digest.
FULL_REFERENCE = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*:[0-9a-f]{64}$")

FAKE_DOCKER = r'''#!__PYTHON__
"""Fake docker for run_host.sh tests. Logs argv; never touches a daemon."""
import json
import os
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\n")

base_id = os.environ["FAKE_DOCKER_BASE_ID"]
tag_id = os.environ.get("FAKE_DOCKER_TAG_ID") or base_id
base_tag = os.environ.get("FAKE_DOCKER_BASE_TAG", "")
lab_ref = os.environ.get("FAKE_DOCKER_LAB_REF", "patrolbot-dashboard:tuning-lab")
lab_id = os.environ.get("FAKE_DOCKER_LAB_ID", "sha256:" + "7" * 64)

if "inspect" in args:
    ref = args[-1]
    if ref == lab_ref:
        print(lab_id)
    elif ref == base_tag:
        print(tag_id)
    else:
        print(base_id)
    raise SystemExit(0)

if "tag" in args:
    raise SystemExit(0)

if "build" in args:
    raise SystemExit(int(os.environ.get("FAKE_DOCKER_BUILD_RC", "0")))

if "run" in args:
    out = os.environ.get("FAKE_DOCKER_RUN_OUT", "")
    if out:
        print(out)
    raise SystemExit(int(os.environ.get("FAKE_DOCKER_RUN_RC", "0")))

raise SystemExit(0)
'''


def _run_wrapper(tmp_path, *, base_id=BASE_ID, tag_id=None, build_rc=0, run_rc=0):
    """Run run_host.sh against a fake docker; return (completed, argv log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(FAKE_DOCKER.replace("__PYTHON__", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log = tmp_path / "docker.log"
    evidence = tmp_path / "evidence"
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_BASE_ID": base_id,
        "FAKE_DOCKER_TAG_ID": tag_id if tag_id is not None else base_id,
        "FAKE_DOCKER_BASE_TAG": BASE_TAG,
        "FAKE_DOCKER_LAB_REF": LAB_REF,
        "FAKE_DOCKER_LAB_ID": LAB_ID,
        "FAKE_DOCKER_BUILD_RC": str(build_rc),
        "FAKE_DOCKER_RUN_RC": str(run_rc),
        "BASE_REF": BASE_REF,
        "EVIDENCE_DIR": str(evidence),
    })
    result = subprocess.run(["bash", str(RUN_HOST)], env=env,
                            capture_output=True, text=True)
    commands = [json.loads(line) for line in log.read_text().splitlines()
                if line.strip()] if log.exists() else []
    return result, commands


def _flag_value(argv, flag):
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _all_values(argv, flag):
    values = []
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            values.append(argv[index + 1])
        elif token.startswith(flag + "="):
            values.append(token.split("=", 1)[1])
    return values


def _index_of(commands, predicate):
    return next(i for i, argv in enumerate(commands) if predicate(argv))


def test_wrapper_builds_from_content_tag_not_bare_digest(tmp_path):
    result, commands = _run_wrapper(tmp_path)
    assert result.returncode == 0, result.stderr

    tag_index = _index_of(commands, lambda a: "image" in a and "tag" in a)
    verify_index = _index_of(commands, lambda a: "inspect" in a and a[-1] == BASE_TAG)
    build_index = _index_of(commands, lambda a: "build" in a)
    run_index = _index_of(commands, lambda a: "run" in a)
    # Tag by exact content ID, re-verify the tag, then build, then run.
    assert tag_index < verify_index < build_index < run_index

    tag_argv = commands[tag_index]
    assert tag_argv[-2] == BASE_ID
    assert tag_argv[-1] == BASE_TAG

    build_argv = commands[build_index]
    assert _flag_value(build_argv, "--network") == "none"
    assert "--pull=false" in build_argv
    nav2 = [value for value in _all_values(build_argv, "--build-arg")
            if value.startswith("PATROLBOT_NAV2_IMAGE=")]
    assert len(nav2) == 1
    image = nav2[0].split("=", 1)[1]
    assert image == BASE_TAG
    assert not image.startswith("sha256:")
    assert FULL_REFERENCE.match(image), image

    run_argv = commands[run_index]
    assert run_argv[-1] == LAB_ID
    assert _flag_value(run_argv, "--network") == "none"
    assert _flag_value(run_argv, "--cap-drop") == "ALL"
    assert _flag_value(run_argv, "--security-opt") == "no-new-privileges"
    assert "--rm" in run_argv

    # The receipt still binds the exact content IDs / refs, not the local tag.
    envs = _all_values(run_argv, "-e") + _all_values(run_argv, "--env")
    assert "TUNING_LAB_BASE_IMAGE_ID=" + BASE_ID in envs
    assert "TUNING_LAB_BASE_IMAGE_REF=" + BASE_REF in envs
    assert "TUNING_LAB_IMAGE_ID=" + LAB_ID in envs
    assert "TUNING_LAB_IMAGE_REF=" + LAB_REF in envs


def test_wrapper_mounts_no_host_or_hardware_paths(tmp_path):
    result, commands = _run_wrapper(tmp_path)
    assert result.returncode == 0, result.stderr

    run_argv = next(argv for argv in commands if "run" in argv)
    joined = " ".join(run_argv)
    assert "--privileged" not in run_argv
    assert "--device" not in run_argv
    assert not any(token.startswith("/dev") for token in run_argv)
    assert "/dev/" not in joined
    assert "/sys/" not in joined
    assert "/proc/" not in joined
    assert "host.docker.internal" not in joined
    assert "--net=host" not in joined
    assert "--network=host" not in joined

    mounts = _all_values(run_argv, "-v") + _all_values(run_argv, "--volume")
    assert mounts == [str(tmp_path / "evidence") + ":/tmp/tuning-lab-evidence"]


def test_tag_id_mismatch_aborts_before_build_and_run(tmp_path):
    result, commands = _run_wrapper(tmp_path, tag_id="sha256:" + "9" * 64)
    assert result.returncode != 0
    assert "resolves to" in result.stderr
    assert not any("build" in argv for argv in commands)
    assert not any("run" in argv for argv in commands)


def test_invalid_base_id_aborts_before_tag_build_and_run(tmp_path):
    result, commands = _run_wrapper(tmp_path, base_id="patrolbot:some-mutable-tag")
    assert result.returncode != 0
    assert "exact sha256 image ID" in result.stderr
    assert not any("image" in argv and "tag" in argv for argv in commands)
    assert not any("build" in argv for argv in commands)
    assert not any("run" in argv for argv in commands)


def test_build_failure_stops_before_run(tmp_path):
    result, commands = _run_wrapper(tmp_path, build_rc=1)
    assert result.returncode != 0
    assert any("build" in argv for argv in commands)
    assert not any("run" in argv for argv in commands)
