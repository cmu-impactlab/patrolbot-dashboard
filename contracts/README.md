# Shared contracts

`web-teleop.json` is the existing fixed wire envelope; it is unchanged by tuning.

The `robot-tuning.*.json`, `validate_robot_tuning.py`, `test_robot_tuning.py` and
`requirements-test.txt` files are byte-for-byte copies from the robot repository
(`cmu-impactlab/patrolbot`, `contracts/`). Edit the canonical files there, review
them, then re-vendor the complete set. Never widen limits in only one repository.

The validator and tests are **development-only, offline tools**. The application
also packages the registry for strict server request validation. These artifacts
do not authorize a writable capability or control: all parameters remain
non-activatable. Guarded signed-mutation/runtime code now exists;
`runtime_implemented: true` records source implementation, not deployed/native
verification or authorization. Runtime capabilities require their actual keys,
store, services and evidence. Live robot configuration remains unverified.

```sh
make test-tuning-contract
PATROLBOT_CORE_REPO=/path/to/patrolbot make test-tuning-contract-parity
```

`TUNING_PY=/path/to/dev-venv/bin/python` selects an isolated test interpreter.
Dependencies are dev/test-only; no application dependencies are added. Parity
checks use an explicit local checkout and never fetch or contact a robot.

See `docs/advanced-tuning-inventory.md`, `docs/advanced-tuning-threat-model.md` and
`docs/advanced-tuning-runtime-verification.md` at the dashboard repository root for
evidence boundaries and the remaining runtime/acceptance gates.
