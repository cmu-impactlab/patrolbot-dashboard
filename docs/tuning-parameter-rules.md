# Tuning values and input bounds

Starting values come from the paired robot repository's current YAML:
`patrolbot_navigation/config/nav2_params.yaml` and the final
`patrolbot-launch/param/defaults/smoother.yaml`. They are not Nav2's example
defaults and must not be labeled as live readback or durably saved before a
robot-owned revision exists. Saved values take precedence after initialization.

Each input must satisfy documented Nav2 Jazzy type/domain/dependency constraints
**and** the robot's tighter reviewed edit policy. Example defaults and local
slider budgets are not universal ROS bounds. Exclusive endpoints remain
exclusive in the UI, server and core; invalid input is not silently clamped.

- AMCL `sigma_hit` must be positive; zero is a degenerate Gaussian denominator.
- RPP `min_approach_linear_velocity` must be strictly greater than `0.01 m/s`.
- AMCL `pf_z` is a positive quantile multiplier, not a probability bounded by
  one: the documented `2.33` is valid; the YAML start remains `0.99`.
- The robot enforces the documented approach-scaling recommendation as
  `< 1.5 m` for its current `3 m` local costmap. This is geometry-specific, not
  a universal upstream number.
- Used laser mixture weights sum to 1; particle/lookahead pairs are coherent.
- The robot retains ordinary-drive caps `0.30 m/s` and `0.70 rad/s`, plus its
  existing conservative resource/tuning budgets. The UI cannot raise them.
- Where upstream publishes no universal numeric upper bound, the UI must not
  claim the local edit range is a ROS requirement or invent a new bound.

Official version-specific references:

- [AMCL](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/others/configuring_amcl/)
- [Regulated Pure Pursuit](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/controller_plugins/configuring_regulated_pp/)
- [Velocity smoother](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/configuring_velocity_smoother/)
- [Behavior server](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/configuring_behavior_server/)

The complete source/domain policy is in the paired robot repository's
`docs/tuning-parameter-rules.md`. Documentation review and unit tests do not
authorize activation or replace later version-specific runtime verification.
