# syntax=docker/dockerfile:1
# Isolated native tuning lab. Builds offline from the locally cached patched-Nav2
# image (no apt, no pip, no network) and installs only the three lab scripts.
# There is no production bring-up, no hardware mount and no SBC connection: the
# entrypoint is the lab runner, which itself refuses to start ROS unless the
# sandbox is network-none / loopback-only / ROS_DOMAIN_ID >= 188.
ARG PATROLBOT_NAV2_IMAGE=patrolbot:joystick-handshake-diagnostic-20261015-r3
FROM ${PATROLBOT_NAV2_IMAGE}

USER root
WORKDIR /opt/dashboard
COPY scripts/tuning_lab.py scripts/check_tuning_receipt.py scripts/run_tuning_lab.sh /opt/dashboard/scripts/

ENV PATROLBOT_ISOLATED_TEST=1 \
    ROS_DOMAIN_ID=188 \
    ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    ROS_LOCALHOST_ONLY=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL io.patrolbot.test-only="advanced-tuning-native-no-physical-acceptance"
# Replace the base image's production entrypoint so the lab script is the command.
ENTRYPOINT ["/bin/bash", "/opt/dashboard/scripts/run_tuning_lab.sh"]
