"""No-motion integration checks for the independent measured-speed channel."""
import pytest

from app.protocol.envelope import decode, encode
from app.protocol.messages import MotionData
from app.settings import Settings
from app.telemetry.hub import TelemetryHub
from app.telemetry.store import Slice
from test_web_teleop import fake_base


@pytest.mark.asyncio
@pytest.mark.parametrize("has_origin", [False, True])
async def test_motion_without_amcl_is_rebroadcast_and_keeps_generation(has_origin):
    hub = TelemetryHub(Settings())
    session = hub.get_or_create_state("unit")
    session.websocket = object()
    session.connected_mono = 0
    base = fake_base(odom_epoch="epoch" if has_origin else None,
                     odom_epoch_generation=1 if has_origin else None)
    session.state.base_state = Slice(base, 1)
    session.state.motion = Slice()
    published = []
    hub.publish = lambda kind, robot, data: published.append((kind, data))
    hub._emit_derived = lambda session: None
    async def ignore(*args):
        return None
    hub._emit_events = ignore
    hub.commands.teleop.changed = ignore
    motion = MotionData(linear_velocity=.1, angular_velocity=-.2, source="raw_odom",
        sample_age_s=.01, receipt_age_s=.01, session_generation=7,
        odom_epoch="epoch", odom_epoch_generation=1)
    envelope, payload = decode(encode("telemetry.motion", "unit", 1, motion))
    await hub.handle_robot_message(session, envelope, payload)
    assert session.state.pose.data is None
    assert session.state.motion.data.linear_velocity == .1
    assert published[0][0] == "telemetry.motion"
    assert published[0][1]["angular_velocity"] == -.2
    old = motion.model_copy(update={"session_generation": 6})
    envelope, payload = decode(encode("telemetry.motion", "unit", 2, old))
    await hub.handle_robot_message(session, envelope, payload)
    assert len(published) == 1
