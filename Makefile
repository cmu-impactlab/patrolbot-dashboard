PY := server/.venv/bin/python
PIP := server/.venv/bin/pip

.PHONY: setup server mock frontend test test-server test-mock test-frontend smoke lint

setup:
	python3 -m venv server/.venv
	$(PIP) install -e './server[dev]'
	$(PIP) install -e ./mock-robot
	cd frontend && npm install

server:
	cd server && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

mock:
	$(PY) -m mock_robot --server ws://localhost:8000/ws/robot --token dev-token

frontend:
	cd frontend && npm run dev

test: test-server test-mock test-bridge test-frontend

test-server:
	cd server && .venv/bin/python -m pytest tests -q

test-mock:
	cd mock-robot && ../server/.venv/bin/python -m pytest tests -q

test-bridge:
	cd ros2_ws/src/patrolbot_web_bridge && ../../../server/.venv/bin/python -m pytest test -q

test-frontend:
	cd frontend && npm run test -- --run

smoke:
	$(PY) scripts/smoke.py
	cd frontend && npm run build

lint:
	$(PY) -m compileall -q server/app mock-robot/mock_robot
