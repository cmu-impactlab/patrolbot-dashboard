"""Keep main protection aligned with the actual unconditional PR checks."""
from itertools import product
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
POLICY = json.loads((ROOT / ".github/main-protection.json").read_text())
REQUIRED_WORKFLOWS = ("ci.yml", "tuning-contract.yml")


def workflow_contexts(filename):
    # BaseLoader preserves `on` as a key and Python versions as strings.
    workflow = yaml.load(
        (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
    )
    assert "pull_request" in workflow["on"]
    # No branch/path/type filters may leave required checks pending on a PR.
    assert workflow["on"]["pull_request"] in ("", {})
    contexts = []
    for job_id, job in workflow["jobs"].items():
        assert "if" not in job, f"{filename}: {job_id} must run on every PR"
        assert job.get("continue-on-error", "false") == "false"
        matrix = job.get("strategy", {}).get("matrix")
        if matrix is None:
            contexts.append(job.get("name", job_id))
        else:
            # Changes to matrix naming/shape need a coordinated policy update.
            assert "name" not in job
            assert "include" not in matrix and "exclude" not in matrix
            assert all(isinstance(values, list) for values in matrix.values())
            contexts.extend(
                f"{job_id} ({', '.join(values)})"
                for values in product(*matrix.values())
            )
    return contexts


def test_required_checks_match_actual_pr_jobs():
    contexts = [name for filename in REQUIRED_WORKFLOWS for name in workflow_contexts(filename)]
    checks = POLICY["required_status_checks"]
    assert checks["strict"] is True
    assert set(checks) == {"strict", "checks"}
    assert len(contexts) == len(set(contexts)) == len(checks["checks"]) == 19
    assert all(set(check) == {"context", "app_id"} for check in checks["checks"])
    assert all(check["app_id"] == 15368 for check in checks["checks"])
    assert {check["context"] for check in checks["checks"]} == set(contexts)


def test_review_and_merge_policy_has_no_bypass():
    assert {key: value for key, value in POLICY.items() if key != "required_status_checks"} == {
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews": True,
            "require_last_push_approval": False,
            "require_code_owner_reviews": False,
            "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
        },
        "required_conversation_resolution": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "restrictions": None,
        "required_linear_history": False,
        "lock_branch": False,
    }


@pytest.mark.parametrize("filename", REQUIRED_WORKFLOWS)
def test_supported_python_cells_remain_required(filename):
    workflow = yaml.load(
        (ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader
    )
    for job in workflow["jobs"].values():
        matrix = job.get("strategy", {}).get("matrix", {})
        if "python-version" in matrix:
            assert set(matrix["python-version"]) == {"3.11", "3.12", "3.13", "3.14"}
