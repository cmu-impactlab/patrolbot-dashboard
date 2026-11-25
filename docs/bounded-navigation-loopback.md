# Bounded autonomous speed loopback

This opt-in test uses the actual Advanced page and dashboard server. Its robot
WebSocket peer is a test fixture with a small deterministic Nav2 command
model, not the native Nav2 controller or deployed web bridge. It does not use
ROS, DDS, or the robot network. No shipped bridge or mock advertises the
capability as a result of this test.

Run from the dashboard checkout with the server environment active, frontend
dependencies installed, Node 22+, and Playwright Chromium installed:

```bash
python scripts/run-bounded-nav-loopback.py
```

The launcher starts the server on an ephemeral loopback port, uses an
ephemeral robot token and temporary SQLite database, and starts the normal
Vite Playwright fixture on localhost. It ignores inherited PatrolBot and
Vite settings. It does not import production settings or run against an
existing dashboard.

The test checks that the real broker and UI:

- confirm a bounded selection while idle without starting a goal;
- do not forward a cap request while the broker owns an active goal;
- reject 1.8 m/s in the current picker and accept Custom at 0.26 m/s after
  cancellation, without resuming the model's stopped goal;
- discard the prior receipt on disconnect; the simulated replacement
  starts at Fast (0.22 m/s), receives no replay, and uses a new session ID.

The test does not establish authority for changes during a real Nav2 goal,
native speed limiting, physical stopping, or a safety field. The current
broker's busy-goal restriction is intentional and remains unchanged.
