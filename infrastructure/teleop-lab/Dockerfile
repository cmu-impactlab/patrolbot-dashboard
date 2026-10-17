FROM patrolbot-dashboard:teleop-test-tools
WORKDIR /opt/dashboard
COPY server /opt/dashboard/server
COPY ros2_ws/src/patrolbot_web_bridge /opt/dashboard/ros2_ws/src/patrolbot_web_bridge
COPY contracts /opt/dashboard/contracts
COPY shared/schemas /opt/dashboard/shared/schemas
COPY frontend/dist /opt/dashboard/server/static
COPY scripts/teleop_lab.py scripts/check_teleop_receipt.py scripts/run_teleop_lab.sh /opt/dashboard/scripts/
RUN . /opt/ros/jazzy/setup.sh && . /opt/patrolbot/install/setup.sh && \
    colcon build --base-paths ros2_ws/src --packages-select patrolbot_web_bridge \
      --build-base /tmp/teleop-build --install-base /opt/web-teleop/install \
      --cmake-args -DBUILD_TESTING=OFF
ENV PYTHONPATH=/opt/dashboard/server \
    PATROLBOT_ISOLATED_TEST=1 \
    ROS_DOMAIN_ID=188 \
    ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
    ROS_LOCALHOST_ONLY=1 \
    PYTHONDONTWRITEBYTECODE=1
LABEL io.patrolbot.test-only="web-teleop-no-physical-acceptance"
# Replace the toolchain's `bash -lc` entrypoint so this script is the command,
# not an extra argument to a login shell that would exit immediately.
ENTRYPOINT ["/bin/bash", "/opt/dashboard/scripts/run_teleop_lab.sh"]
