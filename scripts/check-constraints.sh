#!/usr/bin/env bash
# Do the constraints files still describe what the images install — and does
# the server still pass its tests against that exact set?
#
# Catches the drift that matters: a dependency added to a pyproject.toml
# without regenerating the constraints, a pin left behind for a package nothing
# uses, or a constrained version that the suite does not actually work with. It
# does NOT catch "a newer version exists upstream" — that is the point of
# pinning, and the unconstrained CI matrix is what notices those.
#
# Runs in the same image the Dockerfiles build from, so the answer is about
# production rather than about whoever's laptop.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
status=0

# $1 project directory name, $2 distribution name as pip reports it
check_project() {
    local project="$1" dist="$2"
    local constraints="$REPO/$project/constraints.txt"

    local resolved
    resolved="$(docker run --rm -v "$REPO:/repo:ro" "$IMAGE" bash -c "
        set -euo pipefail
        cp -r /repo/$project /tmp/project
        rm -rf /tmp/project/.venv /tmp/project/*.egg-info
        pip install --quiet --no-cache-dir --root-user-action=ignore \
            -c /tmp/project/constraints.txt /tmp/project >/dev/null
        # Exact distribution name only: a future 'patrolbot-something' package
        # is a real dependency and must not be filtered out silently.
        pip freeze --exclude-editable \
            | grep -vE \"^($dist==| *$dist @ )\" \
            | LC_ALL=C sort
    ")"

    local expected
    expected="$(grep -v '^#' "$constraints" | grep -v '^[[:space:]]*$' | LC_ALL=C sort)"

    if [ "$resolved" = "$expected" ]; then
        echo "OK   $project/constraints.txt matches $IMAGE ($(echo "$expected" | wc -l) packages)"
        return 0
    fi

    echo "FAIL $project/constraints.txt no longer matches the resolved install." >&2
    echo "     '-' only in constraints.txt, '+' only in the real install:" >&2
    diff <(echo "$expected") <(echo "$resolved") | sed 's/^</     -/; s/^>/     +/' >&2 || true
    echo "     Regenerate it as documented at the top of that file, and review" >&2
    echo "     the diff before committing." >&2
    status=1
}

# The Dockerfiles are the reason any of this matters; a constraints file that
# nothing installs against is decoration.
check_dockerfile_uses_constraints() {
    local dockerfile="$1"
    if grep -q -- "-c constraints.txt" "$REPO/$dockerfile"; then
        echo "OK   $dockerfile installs against its constraints"
    else
        echo "FAIL $dockerfile no longer passes -c constraints.txt to pip" >&2
        status=1
    fi
}

# Installing the pinned set is not the same as it working. The dev tools are
# deliberately unconstrained — they are not shipped — but the runtime versions
# under them are exactly the ones production gets.
check_server_tests_against_constraints() {
    echo "     running the server suite against the constrained runtime set..."
    # The suite reads shared/ fixtures and infrastructure/ config by path
    # relative to the repository root, so it needs that layout, not just the
    # server directory.
    if docker run --rm -v "$REPO:/repo:ro" "$IMAGE" bash -c '
        set -euo pipefail
        mkdir -p /tmp/repo
        cp -r /repo/server /repo/shared /repo/infrastructure /tmp/repo/
        rm -rf /tmp/repo/server/.venv /tmp/repo/server/*.egg-info
        pip install --quiet --no-cache-dir --root-user-action=ignore \
            -c /tmp/repo/server/constraints.txt "/tmp/repo/server[dev]" >/dev/null
        cd /tmp/repo/server && python -m pytest tests -m "not external" -q
    ' >/tmp/constrained-tests.log 2>&1; then
        echo "OK   server suite passes on the constrained set"
    else
        echo "FAIL server suite fails on the constrained set:" >&2
        tail -25 /tmp/constrained-tests.log >&2
        status=1
    fi
}

check_project server patrolbot-dashboard-server
check_project mock-robot patrolbot-mock-robot
check_dockerfile_uses_constraints infrastructure/Dockerfile.server
check_dockerfile_uses_constraints infrastructure/Dockerfile.mock
check_server_tests_against_constraints

exit "$status"
