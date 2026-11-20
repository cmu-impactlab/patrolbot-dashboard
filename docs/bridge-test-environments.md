# Bridge test environments

The host CI matrix installs `./server[dev]` from the checkout. This supplies
websockets, pytest-asyncio, pytest-timeout and the server model dependencies.
Run the complete host suite with the checkout's `server` and
`ros2_ws/src/patrolbot_web_bridge` on `PYTHONPATH`; no ROS graph is required.

Native ROS testing uses the test-only dependencies in `package.xml`. The
server is not a robot runtime dependency. In a native image without the
dashboard server stack, explicitly select the bridge-only cases:

```sh
colcon test --packages-select patrolbot_web_bridge --pytest-args \
  -k 'not test_server_to_actual_motor_disable_adapter_through_reset_and_stale_hardware'
```

The two parameterized server/adapter cases excluded there run in the host CI
job. Installing the host server dev environment instead permits the complete
suite, with the same explicit source paths as host CI. Async tests require the
plugin even in strict mode; registering the marker alone does not execute them.
