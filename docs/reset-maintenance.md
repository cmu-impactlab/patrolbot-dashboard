# Reset-only maintenance prototype

No live operation is authorized by this prototype. Commissioning and real reset
acceptance remain separate operator tasks.

The existing fixed-operation Unix supervisor can be exposed without constructing
the bridge's `CommandExecutor` or `RosTeleop`. Set the bridge environment explicitly:

| `WEB_BRIDGE_ENABLE_COMMANDS` | `WEB_BRIDGE_ENABLE_RESET` | Behavior |
| --- | --- | --- |
| unset / `0` | unset / `0` | Read-only; no reset client, even with a socket mounted |
| `0` | `1` | Reset-only maintenance |
| `1` | any | Existing commands-enabled behavior, including reset |

Only the literal `1` enables either switch. Reset still requires
`PATROLBOT_RESET_SOCKET` pointing to the existing, reachable Unix supervisor.
A missing, inaccessible, or invalid socket reply leaves `software_reset`
unadvertised. Socket wiring alone never enables reset on a read-only install.
In the paired core checkout, standard Compose passes the new switch with default
`0`; `docker/.env.example` keeps both switches at `0`. Existing socket directory,
group membership, and supervisor commissioning requirements remain unchanged.

Reset-only does not construct the motion command executor or manual drive
adapter, advertise their capabilities, or dispatch drive, undock, motor power,
charge release, LiDAR changes, pose changes, Stop/cancel, or any other command.
Stop/disable handling in commands-enabled mode is unchanged. Existing independent
tuning inspection/commissioning is unchanged; this switch grants no tuning rights.
The bridge receives no Docker socket, SSH credentials, shell execution, or new
management endpoint. Only the existing host supervisor owns its fixed restart
sequence; a software reset itself intentionally restarts hardware-facing services.

The existing server operator authorization and UI confirmation still apply.
UUID validation and durable supervisor dedupe, single flight, connection-generation
checks, queued-input invalidation, unknown-outcome barriers, and explicit retry
remain in the existing paths. No reset request is replayed or automatically retried.
Status is polled and reported again after reconnection. An unknown outcome is not
success: wait for a terminal supervisor result or explicitly confirm a new reset.
Navigation readiness must be established separately after reset.

This dashboard route requires the dashboard server and web bridge to be reachable.
It is **not recovery when the bridge is down**. Supervisor status may survive a
bridge restart, but a disconnected dashboard cannot submit a new reset. Recovery
of a dead bridge belongs to the separately authorized host maintenance procedure;
this prototype adds no remote recovery facility.

Offline coverage uses a temporary fake Unix supervisor, existing server and UI
tests, and the host supervisor's fake adapter tests. It does not establish live
reset acceptance or permission to operate a robot.
