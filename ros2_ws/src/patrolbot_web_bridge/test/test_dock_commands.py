"""Pure-logic tests for the dock-manager command path (no ROS graph needed).

The translation from the robot's typed result codes into what the operator
reads is the part worth pinning: a wrong mapping here tells someone the robot
is fine when it has actually stopped with a safety fault.

Codes are verbatim from patrolbot_interfaces/action/Undock.action and
patrolbot_interfaces/srv/{ChargeRelease,MotorEnable}.srv.
"""
from patrolbot_web_bridge.commands import (
    SERVICE_CODE_TEXT,
    UNDOCK_CODE_TEXT,
    service_detail,
    undock_detail,
    undock_outcome,
    undock_stage,
)

# Undock.action result codes.
OK = 0
REJECTED = 1
PRECHECK_FAILED = 2
RELEASE_FAILED = 3
MOTOR_STATE_FAILED = 4
SENSOR_FAILED = 5
LOCALIZATION_FAILED = 6
COMMAND_PATH_FAILED = 7
MOTION_FAILED = 8
CANCELED = 9
TIMEOUT = 10
SESSION_CHANGED = 11
SAFETY_FAULT = 12


def test_every_undock_code_has_operator_copy():
    for code in range(OK, SAFETY_FAULT + 1):
        assert code in UNDOCK_CODE_TEXT, f"undock code {code} has no copy"
        assert UNDOCK_CODE_TEXT[code].strip()


def test_every_service_code_has_operator_copy():
    for code in range(0, 8):
        assert code in SERVICE_CODE_TEXT
        assert SERVICE_CODE_TEXT[code].strip()


def test_success_maps_to_succeeded():
    assert undock_outcome(True, OK) == "succeeded"


def test_cancel_and_timeout_are_not_reported_as_failures():
    # The dashboard styles these differently and does not alarm on them.
    assert undock_outcome(False, CANCELED) == "canceled"
    assert undock_outcome(False, TIMEOUT) == "timeout"


def test_every_other_code_is_a_failure():
    for code in (REJECTED, PRECHECK_FAILED, RELEASE_FAILED, MOTOR_STATE_FAILED,
                 SENSOR_FAILED, LOCALIZATION_FAILED, COMMAND_PATH_FAILED,
                 MOTION_FAILED, SESSION_CHANGED, SAFETY_FAULT):
        assert undock_outcome(False, code) == "failed"


def test_success_flag_without_ok_code_is_still_a_failure():
    """Fail closed: a robot claiming success with a non-OK code is not trusted."""
    assert undock_outcome(True, SAFETY_FAULT) == "failed"


def test_detail_carries_the_manager_message_on_failure():
    detail = undock_detail(LOCALIZATION_FAILED, "amcl covariance 0.9")
    assert "where it is" in detail
    assert "amcl covariance 0.9" in detail


def test_detail_omits_the_message_on_success():
    # Nothing useful to add, and the success sentence should stay clean.
    assert undock_detail(OK, "state=COMPLETE") == UNDOCK_CODE_TEXT[OK]


def test_manual_recovery_is_spelled_out():
    detail = undock_detail(MOTION_FAILED, "", manual_recovery=True)
    assert "check the robot at the dock" in detail


def test_unknown_code_still_produces_a_sentence():
    assert undock_detail(999, "").strip()
    assert service_detail(999, "").strip()


def test_service_detail_reports_precondition_failures():
    detail = service_detail(3, "motors already enabled")
    assert "pre-conditions" in detail
    assert "motors already enabled" in detail


def test_undock_stages_are_plain_language():
    assert undock_stage("MOVING") == "Backing off the dock"
    assert undock_stage("releasing") == "Releasing the charger"
    assert undock_stage("TURN_AWAY_FROM_DOCK") == "Turning away from the dock"
    assert undock_stage("HARDWARE_UNDOCK", "BACKING_OUT") == "Backing clear of the dock"
    # An unrecognized state passes through rather than vanishing.
    assert undock_stage("SOME_NEW_STATE") == "SOME_NEW_STATE"
    assert undock_stage("") == "Undocking"
