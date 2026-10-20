"""Dynamic trusted teleop policy: helper validation plus controller integration.

Software-only. Fake hardware/policy evidence exists ONLY in this lab; no ROS,
SSH, production node or physical motion is involved or claimed. The trusted
policy is supplied by a fake ``hub.tuning.teleop_policy`` provider exactly the
way the parent will wire the real adapter.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app.commands import tuning_policy
from app.commands.tuning_policy import (
    HARD_MAX_ANGULAR_RAD_S,
    HARD_MAX_LINEAR_M_S,
    LEGACY_MODE,
    LEGACY_POLICY,
    TeleopPolicy,
    resolve_policy,
)
from app.protocol.envelope import decode, encode
from app.protocol.messages import BaseStateData, TeleopVelocityData
from app.settings import Settings
from app.telemetry.hub import TelemetryHub

ROBOT = "isolated-tuning-software-robot"
GOOD_GENERATION = "2f6f0c6a-7d2a-4d7e-9b2a-000000000001"


# --- fixtures / helpers -----------------------------------------------------


class Socket:
    def __init__(self):
        self.frames = []
        self.delay = False
        self.fail = False

    async def send_text(self, raw):
        if self.fail:
            raise ConnectionError("isolated send failure")
        if self.delay:
            await asyncio.Event().wait()
        self.frames.append(json.loads(raw))

    async def close(self, **kwargs):
        pass


class Provider:
    """Stand-in for ``hub.tuning.teleop_policy``; the parent wires the real one."""

    def __init__(self, policy=None):
        self.policy = policy
        self.error = False
        self.calls = 0

    def __call__(self, robot_id):
        self.calls += 1
        if self.error:
            raise RuntimeError("isolated provider failure")
        return self.policy


def fake_base(**overrides):
    data = dict(session_generation=7, link_connected=True, telemetry_age=.01,
                hardware_state_valid=True, charge_state="idle", motors_enabled=True,
                estop_pressed=False, fault_flags=0, stall_value=0,
                bumpers_front=False, bumpers_rear=False, bumpers_valid=True,
                dock_state="CLEAR_CONFIRMED", dock_state_valid=True,
                odom_epoch_valid=True, raw_odom_age_s=.01,
                lidar_state="READY", lidar_scan_age=.01)
    return BaseStateData(**dict(data, **overrides))


def make_policy(**overrides):
    data = dict(max_linear_m_s=.1, max_angular_rad_s=.2, generation=GOOD_GENERATION,
                received_mono=time.monotonic(), robot_generation=7)
    data.update(overrides)
    return data


def operator(uid=1, can_command=True):
    return SimpleNamespace(id=uid, username=f"operator-{uid}", can_command=can_command)


async def request(lab, command="teleop_start", client=None, command_id=None, **extra):
    command_id = command_id or str(uuid.uuid4())
    env, payload = decode(encode("command.request", ROBOT, 1,
        dict(command_id=command_id, command=command, **extra)))
    await lab.hub.commands.handle_browser_request(client or lab.client, env, payload)
    return command_id


async def reply(lab, command_id, kind="command.ack", **data):
    if not data:
        data = {"accepted": True} if kind == "command.ack" else {"outcome": "canceled"}
    env, payload = decode(encode(kind, ROBOT, 1, dict(command_id=command_id, **data)))
    await lab.hub.handle_robot_command_reply(lab.session, env, payload)


async def velocity(lab, start_id, seq=0, client=None, stamp=None, **changes):
    env, payload = decode(encode("teleop.velocity", ROBOT, seq,
        dict(command_id=start_id, seq=seq, **dict(dict(linear=.05, angular=0., deadman=True), **changes)),
        timestamp=stamp))
    return await lab.hub.commands.teleop.velocity(client or lab.client, env, payload)


def samples(lab):
    return [f for f in lab.robot.frames if f["type"] == "teleop.velocity"]


def browser_frames(client):
    return [json.loads(raw) for raw, _ in client._queue]


@pytest.fixture
async def lab():
    hub = TelemetryHub(Settings(_env_file=None, default_robot_id=ROBOT))
    robot = Socket()
    session = await hub.robot_connected(ROBOT, robot)
    session.state.set_capabilities(["web_teleop_v1", "robot_tuning_v1"])
    session.state.record_base_state(fake_base())
    provider = Provider(make_policy())
    # Same attribute the real adapter exposes; `provider_from_hub` discovers it.
    hub.tuning.teleop_policy = provider
    client = await hub.browser_connected(Socket(), user=operator())
    value = SimpleNamespace(hub=hub, robot=robot, session=session, client=client,
                            provider=provider)
    try:
        yield value
    finally:
        robot.delay = False
        robot.fail = False
        await hub.stop()


async def started(lab):
    start_id = await request(lab)
    assert lab.robot.frames[-1]["data"]["command_id"] == start_id
    await reply(lab, start_id)
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "active"
    return start_id


# --- helper: capability gate ------------------------------------------------


@pytest.mark.parametrize("capabilities,expected", [
    (["web_teleop_v1"], False),
    (["robot_tuning_read_v1"], False),                       # read alone never requires
    (["web_teleop_v1", "robot_tuning_read_v1"], False),
    (["robot_tuning_v1"], True),
    (["robot_tuning_policy_v1"], True),
    (["web_teleop_v1", "robot_tuning_v1", "robot_tuning_read_v1"], True),
    ([], False),
    (None, False),
    ("robot_tuning_v1", True),                               # tolerant of a string set
])
def test_capability_requires_policy(capabilities, expected):
    assert tuning_policy.capability_requires_policy(capabilities) is expected


def test_legacy_policy_matches_commissioned_envelope():
    assert LEGACY_POLICY.mode == LEGACY_MODE
    assert LEGACY_POLICY.limits == (HARD_MAX_LINEAR_M_S, HARD_MAX_ANGULAR_RAD_S)
    assert LEGACY_POLICY.limits == (0.30, 0.70)
    assert LEGACY_POLICY.dynamic is False


def test_legacy_resolution_never_consults_a_policy_source():
    boom = Provider(make_policy())
    policy, reason = resolve_policy(capabilities=["web_teleop_v1"], provider=boom,
                                    robot_generation=7)
    assert reason is None and policy is LEGACY_POLICY
    assert boom.calls == 0


# --- helper: parse_policy ---------------------------------------------------


def test_valid_policy_round_trips_reduced_caps():
    now = time.monotonic()
    policy, reason = tuning_policy.parse_policy(make_policy(received_mono=now), now=now)
    assert reason is None
    assert policy.mode == tuning_policy.DYNAMIC_MODE
    assert policy.limits == (.1, .2)
    assert policy.generation == GOOD_GENERATION
    assert policy.robot_generation == 7
    assert policy.age(now=now) == 0.0
    assert not policy.over_policy(.1, .2)
    assert policy.over_policy(.1000001, .2)


def test_dataclass_policy_is_accepted():
    raw = TeleopPolicy(mode=tuning_policy.DYNAMIC_MODE, max_linear_m_s=.2,
                       max_angular_rad_s=.3, generation="sha256:abc123",
                       received_mono=time.monotonic(), robot_generation=7)
    policy, reason = tuning_policy.parse_policy(raw)
    assert reason is None and policy.signature == raw.signature


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"),
                                   True, False, "0.1", None])
def test_nonfinite_or_wrong_typed_caps_refused(value):
    policy, reason = tuning_policy.parse_policy(make_policy(max_linear_m_s=value))
    assert policy is None and reason


def test_huge_int_cap_is_refused_not_raised():
    # 10**400 is an int but overflows float(); it must refuse, never crash.
    policy, reason = tuning_policy.parse_policy(make_policy(max_linear_m_s=10 ** 400))
    assert policy is None and "finite" in reason
    policy, reason = tuning_policy.parse_policy(make_policy(received_mono=10 ** 400))
    assert policy is None and reason


@pytest.mark.parametrize("linear,angular", [
    (.301, .7), (.3, .701), (.31, .71), (1.0, 1.0), (.3, 3.0),
])
def test_caps_above_commissioned_envelope_refused(linear, angular):
    policy, reason = tuning_policy.parse_policy(make_policy(max_linear_m_s=linear,
                                                           max_angular_rad_s=angular))
    assert policy is None and "envelope" in reason


@pytest.mark.parametrize("linear", [-0.01, -1.0])
def test_negative_caps_refused(linear):
    policy, reason = tuning_policy.parse_policy(make_policy(max_linear_m_s=linear))
    assert policy is None and "envelope" in reason


@pytest.mark.parametrize("generation", [
    None, "", "  ", "/etc/passwd", "..\\..\\win", "a" * 129,
    "gen/1", "café-1", "gen-١٢٣", "１２３", "----", "\u2028gen",
])
def test_noncanonical_generation_refused(generation):
    policy, reason = tuning_policy.parse_policy(make_policy(generation=generation))
    assert policy is None and "generation" in reason


@pytest.mark.parametrize("generation", [
    "gen-1", GOOD_GENERATION, "sha256:deadbeef", "rev.2026-10-17",
    "boot_0001", "2026-10-17T00:00:00Z",
])
def test_canonical_generations_accepted(generation):
    policy, reason = tuning_policy.parse_policy(make_policy(generation=generation))
    assert reason is None and policy.generation == generation


def test_stale_and_future_receipts_refused():
    now = time.monotonic()
    policy, reason = tuning_policy.parse_policy(
        make_policy(received_mono=now - tuning_policy.POLICY_MAX_AGE_S - .01), now=now)
    assert policy is None and "stale" in reason
    policy, reason = tuning_policy.parse_policy(
        make_policy(received_mono=now - tuning_policy.POLICY_MAX_AGE_S + .5), now=now)
    assert reason is None  # still inside the window
    policy, reason = tuning_policy.parse_policy(
        make_policy(received_mono=now + tuning_policy.POLICY_FUTURE_TOLERANCE_S + .01), now=now)
    assert policy is None and "future" in reason
    policy, reason = tuning_policy.parse_policy(
        make_policy(received_mono=now + tuning_policy.POLICY_FUTURE_TOLERANCE_S - .01), now=now)
    assert reason is None  # small clock wobble tolerated


@pytest.mark.parametrize("bound", [True, False, -1, 1.0, "7", None])
def test_invalid_or_missing_session_binding_refused(bound):
    policy, reason = tuning_policy.parse_policy(make_policy(robot_generation=bound))
    assert policy is None and reason


def test_unknown_and_missing_mapping_fields_refused():
    policy, reason = tuning_policy.parse_policy({**make_policy(), "extra": 1})
    assert policy is None and "unknown" in reason
    raw = make_policy()
    del raw["generation"]
    policy, reason = tuning_policy.parse_policy(raw)
    assert policy is None and "missing" in reason


def test_none_policy_is_refused():
    policy, reason = tuning_policy.parse_policy(None)
    assert policy is None and reason


# --- helper: resolve_policy -------------------------------------------------


def test_tuning_capability_without_policy_is_refused():
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], provider=Provider(None),
                                    robot_generation=7)
    assert policy is None and "refused" in reason


def test_provider_exception_is_refused_not_raised():
    provider = Provider(make_policy())
    provider.error = True
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], provider=provider,
                                    robot_generation=7)
    assert policy is None and "could not be read" in reason


def test_provider_takes_precedence_over_state_attribute():
    state = SimpleNamespace(tuning_policy=make_policy(max_linear_m_s=.5,
                                                      generation="stale-state-gen"))
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], state=state,
                                    provider=Provider(make_policy()), robot_generation=7)
    assert reason is None and policy.limits == (.1, .2)
    assert policy.generation == GOOD_GENERATION


def test_state_attribute_interface_used_without_provider():
    state = SimpleNamespace(tuning_policy=make_policy(max_linear_m_s=.25))
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], state=state,
                                    robot_generation=7)
    assert reason is None and policy.max_linear_m_s == .25


def test_session_generation_mismatch_refused():
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], provider=Provider(make_policy()),
                                    robot_generation=8)
    assert policy is None and "session" in reason
    policy, reason = resolve_policy(capabilities=["robot_tuning_v1"], provider=Provider(make_policy()),
                                    robot_generation=None)
    assert policy is None and "session" in reason


# --- controller integration -------------------------------------------------


async def test_reduced_policy_rejects_frame_above_the_old_higher_default(lab):
    start_id = await started(lab)
    # The reduced cap is 0.10; the legacy default was 0.30. A frame between the
    # two must be refused, never silently clamped or streamed.
    assert not await velocity(lab, start_id, linear=.2)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert browser_frames(lab.client)  # a terminal/cancel notice reached the tab


async def test_frame_within_reduced_policy_is_forwarded(lab):
    start_id = await started(lab)
    assert await velocity(lab, start_id, linear=.1, angular=.2)
    assert samples(lab)[-1]["data"]["linear"] == .1


async def test_start_refused_when_required_policy_missing(lab):
    lab.provider.policy = None
    await request(lab)
    assert not lab.robot.frames
    assert ROBOT not in lab.hub.commands.teleop.owners


async def test_start_refused_when_provider_errors(lab):
    lab.provider.error = True
    await request(lab)
    assert not lab.robot.frames
    assert ROBOT not in lab.hub.commands.teleop.owners


async def test_stale_policy_refused_at_start(lab):
    lab.provider.policy = make_policy(
        received_mono=time.monotonic() - tuning_policy.POLICY_MAX_AGE_S - 1)
    await request(lab)
    assert not lab.robot.frames
    assert ROBOT not in lab.hub.commands.teleop.owners


async def test_out_of_envelope_policy_refused_at_start(lab):
    lab.provider.policy = make_policy(max_linear_m_s=.9)
    await request(lab)
    assert not lab.robot.frames
    assert ROBOT not in lab.hub.commands.teleop.owners


async def test_policy_generation_rotation_ends_session_without_rearm(lab):
    start_id = await started(lab)
    assert await velocity(lab, start_id, seq=0)
    lab.provider.policy = make_policy(generation="3f6f0c6a-7d2a-4d7e-9b2a-000000000002")
    await lab.hub.commands.teleop.changed(lab.session)
    # Ownership is retained until the robot's terminal result; the session is
    # no longer active and a zero was requested on the still-bound socket.
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False
    # Rotating the policy never re-arms; a fresh explicit start is required.
    assert not await velocity(lab, start_id, seq=1)


async def test_cap_mutation_without_generation_rotation_still_ends_session(lab):
    start_id = await started(lab)
    lab.provider.policy = make_policy(max_linear_m_s=.05,
                                      generation=GOOD_GENERATION)
    await lab.hub.commands.teleop.changed(lab.session)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False


async def test_base_session_generation_rotation_ends_session(lab):
    start_id = await started(lab)
    lab.session.state.record_base_state(fake_base(session_generation=8))
    await lab.hub.commands.teleop.changed(lab.session)
    # The bound socket/session identity is gone, so no zero can be addressed to
    # it; the owner is held unknown rather than silently re-armed.
    assert lab.hub.commands.teleop.owners[ROBOT].phase == "unknown"
    assert not await velocity(lab, start_id, seq=1)


async def test_staleness_during_session_ends_without_rearm(lab):
    start_id = await started(lab)
    lab.provider.policy = make_policy(
        received_mono=time.monotonic() - tuning_policy.POLICY_MAX_AGE_S - 1)
    assert not await velocity(lab, start_id, seq=0)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False


async def test_provider_error_during_session_ends_without_rearm(lab):
    start_id = await started(lab)
    lab.provider.error = True
    await lab.hub.commands.teleop.changed(lab.session)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False


async def test_release_always_reaches_robot_even_when_policy_vanishes(lab):
    start_id = await started(lab)
    assert await velocity(lab, start_id, seq=0)
    lab.provider.policy = None      # policy health is gone mid-session
    before = len(samples(lab))
    # A release bypasses policy health entirely: a zero is never gated on it.
    assert await velocity(lab, start_id, seq=1, deadman=False, linear=0.)
    assert len(samples(lab)) == before + 1
    assert samples(lab)[-1]["data"]["deadman"] is False


async def test_watchdog_revokes_with_policy_present(lab):
    start_id = await started(lab)
    assert await velocity(lab, start_id, seq=0)
    await asyncio.sleep(.37)
    assert lab.hub.commands.teleop.owners[ROBOT].phase != "active"
    assert samples(lab)[-1]["data"]["deadman"] is False
    assert start_id in lab.hub.commands.active  # no fake terminal success


async def test_legacy_robot_still_uses_fixed_envelope(lab):
    lab.session.state.set_capabilities(["web_teleop_v1"])
    lab.provider.policy = make_policy(max_linear_m_s=.05)  # ignored for legacy
    start_id = await started(lab)
    # The commissioned fixed envelope is unchanged for a legacy robot, so the
    # full 0.30 / 0.70 is still accepted even though a dynamic robot would cap it.
    assert await velocity(lab, start_id, linear=.3, angular=.7)
    assert samples(lab)[-1]["data"]["linear"] == .3
