"""Live observation is distinct from saved/verified configuration authority."""
import copy

import pytest

from app.tuning.errors import TuningInvalid
from app.tuning.validation import validate_snapshot
from tuning_fixtures import snapshot


def uninitialized():
    return snapshot(session_id=None, desired_revision=None, active_revision=None,
                    active_known=False, writable=False, activation_supported=False,
                    active={}, effective_limits=None,
                    safe_apply={"allowed": False, "reason": "Not initialized."},
                    readback={"source": "ros_parameters", "age_s": .1,
                              "hardware_session_generation": 1,
                              "values": {"amcl.alpha1": .123, "max_linear_m_s": .3,
                                         "max_angular_rad_s": .7},
                              "limits": {"max_linear_m_s": .3, "max_angular_rad_s": .7}})


def test_uninitialized_readback_does_not_claim_a_saved_or_active_revision():
    data = uninitialized()
    assert validate_snapshot(data)["readback"]["values"]["amcl.alpha1"] == .123
    assert data["active_known"] is False
    assert data["desired_revision"] is None


@pytest.mark.parametrize("field,value", [("age_s", -1), ("age_s", 4),
                                        ("age_s", float("nan")),
                                        ("hardware_session_generation", True),
                                        ("hardware_session_generation", -1)])
def test_invalid_observation_freshness_or_generation_is_rejected(field, value):
    data = uninitialized()
    data["readback"][field] = value
    with pytest.raises(TuningInvalid):
        validate_snapshot(data)


def test_runtime_drift_is_visible_but_cannot_raise_a_gauge_or_policy_ceiling():
    data = uninitialized()
    data["readback"]["values"]["max_linear_m_s"] = .4
    data["readback"]["limits"] = None
    assert validate_snapshot(data)["readback"]["values"]["max_linear_m_s"] == .4
    bad = copy.deepcopy(data)
    bad["readback"]["limits"] = {"max_linear_m_s": .4, "max_angular_rad_s": .7}
    with pytest.raises(TuningInvalid):
        validate_snapshot(bad)


def test_unknown_or_mistyped_values_and_unobserved_caps_are_rejected():
    for values in ({"unknown.id": .2}, {"amcl.alpha1": True},
                   {"amcl.alpha1": float("inf")}, {"max_linear_m_s": .2}):
        data = uninitialized()
        data["readback"]["values"] = values
        with pytest.raises(TuningInvalid):
            validate_snapshot(data)


def test_observation_alone_cannot_satisfy_trusted_teleop_policy():
    from types import SimpleNamespace as NS
    from app.tuning.broker import TuningBroker

    session = NS(robot_id="patrolbot-01", generation=1, websocket=object())
    broker = TuningBroker(NS(robots={session.robot_id: session}))
    broker._policies[session.robot_id] = (session, session.websocket, uninitialized(), 0.)
    assert broker.teleop_policy(session.robot_id) is None
