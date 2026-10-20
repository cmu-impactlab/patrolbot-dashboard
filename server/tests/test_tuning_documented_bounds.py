"""The server enforces the same Nav2 domains and robot edit limits as the core."""

import pytest

from app.tuning import validation
from app.tuning.errors import TuningInvalid
from tuning_fixtures import change, save_request


@pytest.mark.parametrize("pid,bad,good", [
    ("amcl.sigma_hit", 0.0, .2),
    ("nav2.controller.min_approach_linear_velocity", .01, .08),
    ("nav2.controller.approach_velocity_scaling_dist", 1.5, .6),
    ("amcl.pf_z", 0.0, 2.33),
    ("amcl.min_particles", 0, 1000),
    ("amcl.max_beams", 1, 60),
    ("max_linear_m_s", .31, .30),
    ("max_angular_rad_s", .71, .70),
])
def test_server_rejects_documented_boundary_and_robot_limit_violations(pid, bad, good):
    with pytest.raises(TuningInvalid, match="."):
        validation.validate_request(save_request([change(pid, bad)]))
    # Type/range acceptance is separate from cross-field dependency checks.
    entry = validation.registry_index(validation.load_registry())[pid]
    validation._check_value(entry, good, where=pid)
