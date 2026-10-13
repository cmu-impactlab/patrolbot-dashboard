import pytest
from pydantic import ValidationError
from app.protocol.messages import CommandRequestData


def test_measured_profile_is_preserved_and_optional():
    assert CommandRequestData(command_id='x',command='set_initial_pose',seed_profile='measured').model_dump()['seed_profile']=='measured'
    assert CommandRequestData(command_id='x',command='set_initial_pose').seed_profile is None


@pytest.mark.parametrize('command,profile',[('navigate_to_pose','measured'),('set_initial_pose','tight'),('set_initial_pose',True)])
def test_seed_profile_rejected_for_invalid_value_or_other_command(command,profile):
    with pytest.raises(ValidationError):
        CommandRequestData(command_id='x',command=command,seed_profile=profile)
