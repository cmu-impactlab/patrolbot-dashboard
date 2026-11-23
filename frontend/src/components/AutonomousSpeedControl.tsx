import { AutonomousSpeedPicker, displaySpeed } from "./AutonomousSpeedPicker";
import { canCommand, useAuthStore } from "../stores/authStore";
import { autonomousSpeedBinding, useAutonomousSpeedStore } from "../stores/autonomousSpeedStore";
import { useTelemetryStore } from "../stores/telemetryStore";

export function AutonomousSpeedControl() {
  const binding = useTelemetryStore(autonomousSpeedBinding);
  const user = useAuthStore((state) => state.user);
  const mayCommand = canCommand(user);
  const result = useAutonomousSpeedStore((state) => state.result);
  if (!binding && result.phase === "idle") return null;

  return <section aria-label="Autonomous speed profiles">
    {binding && <AutonomousSpeedPicker key={`${binding.session}:${user?.id}:${mayCommand}`} connected
      disabled={!mayCommand || result.phase === "pending"}
      onStage={(selection) => useAutonomousSpeedStore.getState().send(binding, selection)} />}
    {binding && !mayCommand && <p>Navigation command permission is required to change profiles.</p>}
    <p role="status">
      {result.phase === "pending" && binding ? "Waiting for the robot to confirm the caps."
        : result.phase === "applied" && binding
          ? `Robot confirmed this request: Applied ${result.selection.profile} — ${displaySpeed(result.selection)}.`
          : "Applied autonomous speed: unknown."}
    </p>
  </section>;
}
