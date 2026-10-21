# Tuning readback and gauge follow-up

The frontend accepts a valid uninitialized robot snapshot instead of discarding
it solely because its saved session/revision is null. It shows the robot's live
`GetParameters` observations separately from desired YAML starting values, saved
revisions and verified revision values. Missing/stale observations stay unknown;
they are never filled with registry defaults or displayed as stopped speed.

The optional `readback` object has `source: "ros_parameters"`, `age_s`,
`hardware_session_generation`, `values` and `limits`. The server validates its
types, fixed logical IDs, freshness and matching bounded cap observations. The
browser adds a conservative HTTP round-trip age and local elapsed time; readback
expires after three seconds and cannot cross hardware generations. Observations
are display-only and cannot satisfy the trusted teleop-policy/revision contract.

Measured speed remains raw wheel odometry, independent of localization, motor
enablement and tuning initialization. Its freshness is evaluated using the
current render clock, not a cached 500 ms timer timestamp that can predate an
incoming sample. Genuine future, stale, non-finite or old-session data is still
rejected. Signed bands follow their needles; unknown readings/caps have no
misleading zero needle.

Editing/saving desired values requires an administrator, a commissioned signed
save path and a fresh SBC link—not motors, localization or navigation idle.
`write_disabled_reason` explains a missing store/key/link. Applying/redeploying is
separate and retains safe-idle checks. No key, store initialization, motor enable,
pose, goal or live setting change is performed by this source fix.
