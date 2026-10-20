# Tuning lab (isolated native evidence)

Isolated, `--network none`, hardware-free harness that proves the FINAL
`teleop_velocity_smoother` behavior and the curated AMCL / Nav2-RPP startup
configuration against the locally cached **patched Nav2** image. It is not live
Pi evidence and it never commands a robot.

- `Dockerfile` — builds only a COPY layer on top of the cached patched-Nav2
  image. No apt, no pip, no network.
- `run_host.sh` — host-side wrapper: offline build, exact image-ID resolution,
  then `docker run --rm --network none`.
- `../scripts/tuning_lab.py` — the native harness (isolation guard first).
- `../scripts/check_tuning_receipt.py` — strict receipt validator + `--self-test`.
- `../scripts/run_tuning_lab.sh` — in-container runner (image entrypoint).

## Run

```sh
BASE_REF=patrolbot:joystick-handshake-diagnostic-20261015-r3 \
EVIDENCE_DIR=/tmp/opencode/tuning-evidence \
bash infrastructure/tuning-lab/run_host.sh
```

The run is green only when the receipt validates. Evidence:

- `/tmp/tuning-lab-evidence/receipt.json` — machine-checkable receipt.
- `/tmp/tuning-lab-evidence/native.log` — native node logs.
- `amcl_startup.yaml`, `controller_startup.yaml` — generated startup YAML.

## Validity gates

- Nothing runs unless `PATROLBOT_ISOLATED_TEST=1`, `ROS_DOMAIN_ID>=188`,
  `ROS_LOCALHOST_ONLY=1`, and `/sys/class/net` contains only `lo`. The guard is
  the first statement of the run, before any `rclpy` import or publisher.
- The image entrypoint is the lab script; there is no production bring-up.
- Synthetic `Twist` is published only inside the container; it is never a
  hardware input and never a motion command.

## Checker self-test

```sh
python3 scripts/check_tuning_receipt.py --self-test
```

Rejects fabricated/stale receipts (missing image IDs, non-loopback isolation,
AMCL claimed `live_verified`, hot-reload claimed without effect, creep toward the
cap, over-cap "pass", inconsistent summary, stale timestamps).

## Unit tests

```sh
python3 -m pytest scripts/tests/test_tuning_lab_tools.py -q
```

See `docs/advanced-tuning-native-evidence.md` for the recorded results and the
remaining evidence holes.
