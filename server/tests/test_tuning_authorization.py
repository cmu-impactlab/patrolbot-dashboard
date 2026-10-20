"""Signed mutations use the same canonical bytes as the robot core.

The server is the only signer: it derives the actor from the verified session,
binds the claims to one robot and one short lifetime, and the core independently
verifies the exact bytes. These tests lock down the wire vector, the tamper
detection, the expiry/robot binding and the fail-closed key handling.
"""

import copy

import pytest

from app.tuning.authorization import (
    MAX_BYTES,
    MAX_LIFETIME_MS,
    AuthorizationError,
    load_key,
    sign,
    verify,
)


KEY = b'unit-test-secret-not-for-deployment-123456'
WRAPPER = {'actor': {'id': 1, 'username': 'admin', 'role': 'administrator'},
           'request': {'operation': 'save', 'request_id': 'fixture',
                       'changes': [{'id': 'amcl.alpha1', 'value': .1}]}}
SIGNATURE = 'a4e0578045fdf029613dc151caef9c0365c4000ea1576ed19563be8faa6745ca'


def test_server_matches_core_wire_vector():
    signed = sign(WRAPPER, KEY, 'robot-1', now=100)
    assert signed['authorization']['signature'] == SIGNATURE
    assert verify(signed, KEY, 'robot-1', now=101) == WRAPPER


def test_signed_envelope_is_detached_and_robot_bound():
    original = copy.deepcopy(WRAPPER)
    signed = sign(original, KEY, 'robot-1', now=100)
    # The returned body must not alias caller-owned request/actor dictionaries.
    original['request']['operation'] = 'restore'
    assert verify(signed, KEY, 'robot-1', now=101) == WRAPPER
    with pytest.raises(AuthorizationError):
        verify(signed, KEY, 'robot-2', now=101)


def test_lifetime_is_bounded():
    signed = sign(WRAPPER, KEY, 'robot-1', now=100)
    claims = signed['authorization']
    assert claims['expires_at_ms'] - claims['issued_at_ms'] == MAX_LIFETIME_MS


@pytest.mark.parametrize('field', ['actor', 'request', 'authorization'])
def test_mutating_a_server_signed_envelope_is_detected(field):
    body = sign(WRAPPER, KEY, 'robot-1', now=100)
    body[field]['injected'] = True
    with pytest.raises(AuthorizationError):
        verify(body, KEY, 'robot-1', now=101)


@pytest.mark.parametrize('now', [130, 200, 90])
def test_expired_or_future_authorization_is_refused(now):
    with pytest.raises(AuthorizationError):
        verify(sign(WRAPPER, KEY, 'robot-1', now=100), KEY, 'robot-1', now=now)


@pytest.mark.parametrize('key', [None, b'', b'short', 'not-bytes'])
def test_no_default_or_weak_key_can_authorize_a_mutation(key):
    with pytest.raises(AuthorizationError):
        sign(WRAPPER, key, 'robot-1')


def test_wrong_key_is_refused():
    with pytest.raises(AuthorizationError):
        verify(sign(WRAPPER, KEY, 'robot-1'), b'x' * 32, 'robot-1')


def test_nonfinite_payload_is_never_signed():
    body = copy.deepcopy(WRAPPER)
    body['request']['changes'][0]['value'] = float('nan')
    with pytest.raises(AuthorizationError):
        sign(body, KEY, 'robot-1')


def test_oversized_payload_is_never_signed():
    body = copy.deepcopy(WRAPPER)
    body['request']['padding'] = 'x' * (MAX_BYTES + 1)
    with pytest.raises(AuthorizationError):
        sign(body, KEY, 'robot-1')


@pytest.mark.parametrize('claims', [
    {'version': True},
    {'version': 2},
    {'robot_id': 'someone-else'},
    {'issued_at_ms': '100'},
    {'expires_at_ms': None},
    {'issued_at_ms': 100, 'expires_at_ms': 100 + MAX_LIFETIME_MS + 1},
    {'issued_at_ms': 200, 'expires_at_ms': 100},
    {'issued_at_ms': -5, 'expires_at_ms': 100},
])
def test_invalid_authorization_claims_are_refused(claims):
    body = sign(WRAPPER, KEY, 'robot-1', now=100)
    body['authorization'].update(claims)
    with pytest.raises(AuthorizationError):
        verify(body, KEY, 'robot-1', now=101)


def test_missing_or_extra_authorization_claims_are_refused():
    body = sign(WRAPPER, KEY, 'robot-1', now=100)
    for mutate in (
        lambda b: b['authorization'].pop('signature'),
        lambda b: b['authorization'].update(extra=1),
        lambda b: b.pop('authorization'),
    ):
        broken = copy.deepcopy(body)
        mutate(broken)
        with pytest.raises(AuthorizationError):
            verify(broken, KEY, 'robot-1', now=101)


def test_observer_or_unsigned_admin_cannot_authorize_a_change():
    body = copy.deepcopy(WRAPPER)
    body['actor']['role'] = 'observer'
    with pytest.raises(AuthorizationError):
        sign(body, KEY, 'robot-1')
    with pytest.raises(AuthorizationError):
        verify(WRAPPER, KEY, 'robot-1')


def test_private_key_file_must_be_private_and_regular(tmp_path):
    path = tmp_path / 'key'
    path.write_bytes(KEY)
    path.chmod(0o600)
    assert load_key(path) == KEY
    path.chmod(0o644)
    with pytest.raises(AuthorizationError):
        load_key(path)
    path.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises((AuthorizationError, OSError)):
        load_key(link)
    with pytest.raises(AuthorizationError):
        load_key('')
