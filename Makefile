PY := server/.venv/bin/python
PIP := server/.venv/bin/pip

# Interpreter for the shared robot-tuning contract checks. Defaults to the
# server venv; override it to run in a prepared dev-only venv, e.g.
#   make test-tuning-contract TUNING_PY=/tmp/opencode/tuning-contract-venv/bin/python
TUNING_PY ?= $(PY)

.PHONY: setup server mock frontend test test-server test-server-external test-mock test-bridge test-frontend test-tuning-contract test-tuning-contract-parity smoke lint

setup:
	python3 -m venv server/.venv
	$(PIP) install -e './server[dev]'
	$(PIP) install -e './mock-robot[dev]'
	cd frontend && npm install

server:
	cd server && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

mock:
	$(PY) -m mock_robot --server ws://localhost:8000/ws/robot --token dev-token

frontend:
	cd frontend && npm run dev

# Default run is hermetic: no external services, bounded, progress-printing.
test: test-server test-mock test-bridge test-frontend

test-server:
	cd server && .venv/bin/python -m pytest tests -m "not external"

# Opt-in external-service tests (PostgreSQL). Requires PATROLBOT_TEST_PG_URL.
test-server-external:
	cd server && .venv/bin/python -m pytest tests -m external

test-mock:
	cd mock-robot && ../server/.venv/bin/python -m pytest tests -q

test-bridge:
	cd ros2_ws/src/patrolbot_web_bridge && ../../../server/.venv/bin/python -m pytest test -q

test-frontend:
	cd frontend && npm run test -- --run

# Shared robot-tuning contract checks. Installs the dev-only contract test
# requirements (pytest/jsonschema/PyYAML, no application dependencies), then
# runs BOTH the parity tool's unit tests and the vendored canonical contract
# self-tests (schema/registry/validator/fixtures). This is what runs on every
# change; the schema is not gated behind a manual dispatch.
test-tuning-contract:
	$(TUNING_PY) -m pip install -r contracts/requirements-test.txt
	$(TUNING_PY) -m pytest scripts/tests -q
	$(TUNING_PY) -m pytest contracts/test_robot_tuning.py -q

# Byte-for-byte parity of the vendored tuning contract against a LOCAL robot
# checkout that carries contracts/robot-tuning.*. Never fetches anything, so
# the path is explicit: PATROLBOT_CORE_REPO=/path/to/patrolbot make test-tuning-contract-parity
test-tuning-contract-parity:
	$(TUNING_PY) scripts/check_tuning_contract.py --core-repo "$${PATROLBOT_CORE_REPO:?set PATROLBOT_CORE_REPO to the robot repo path}"

smoke:
	$(PY) scripts/smoke.py
	cd frontend && npm run build

lint:
	$(PY) -m compileall -q server/app mock-robot/mock_robot
