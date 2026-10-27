import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useCommandStore } from "../stores/commandStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTeleopStore } from "../stores/teleopStore";
import { useIsFresh } from "../lib/freshness";
import { operationFaultReason } from "../lib/operationFault";
import { motionFreshness, useMotion } from "../lib/motion";

/** Keep failed controller operations visible even though no browser issued them. */
export function OperationFaultBanner() {
  const base = useTelemetryStore(s => s.baseState);
  const ws = useTelemetryStore(s => s.wsConnected);
  const connection = useTelemetryStore(s => s.connection);
  const capabilities = useTelemetryStore(s => s.capabilities);
  const fresh = useIsFresh(useTelemetryStore(s => s.baseStateAt), 3000);
  const motion = useMotion();
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick(value => value + 1), 250);
    return () => clearInterval(timer);
  }, []);
  const speed = motionFreshness(motion, performance.now(), base?.session_generation ?? null);
  const mayCommand = canCommand(useAuthStore(s => s.user));
  const active = useCommandStore(s => s.active);
  const lastResult = useCommandStore(s => s.lastResult);
  const send = useCommandStore(s => s.send);
  const manualBusy = useTeleopStore(s => s.phase !== "idle" || s.externalBusy);
  const [open, setOpen] = useState(false);
  const reason = operationFaultReason(base);
  if (!reason) return null;
  const blocked = !mayCommand ? "Operator permission is required."
    : !ws || connection.state !== "online" || !fresh || !base?.link_connected || !base.hardware_state_valid || !Number.isFinite(base.telemetry_age) || base.telemetry_age < 0 || base.telemetry_age > 2 ? "Wait for fresh, valid hardware readings."
    : !capabilities.includes("dock_reset") ? "Update the robot bridge to enable explicit fault recovery."
    : base.estop_pressed || base.fault_flags || (base.stall_value & 0x0101) || base.operation_outcome_unknown || base.lidar_state === "FAULT" ? "Resolve the physical safety condition; it cannot be bypassed."
    : active || manualBusy || base.undock_active ? "Release manual controls and wait for all active operations to finish."
    : !base.dock_state_valid || !["FAULT_STOPPED", "CLEAR_CONFIRMED", "DOCKED_CONFIRMED"].includes(base.dock_state ?? "") ? "Wait for a confirmed physical dock state."
    : !speed.fresh || speed.linear === null || speed.angular === null || Math.abs(speed.linear) > .02 || Math.abs(speed.angular) > .05 ? "Wait for fresh confirmation that the robot is stationary."
    : null;
  return <section role="alert" className="mx-4 my-2 rounded border border-amber-500 bg-amber-950/30 p-3 text-sm">
    <strong>Robot operation fault</strong>
    <p>{reason}</p>
    {!fresh && <p>These are the last received readings; current state is unconfirmed.</p>}
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild><button className="mt-2 rounded border px-3 py-1">Details / recovery</button></Dialog.Trigger>
      <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded border bg-slate-950 p-5 text-slate-100">
          <Dialog.Title className="font-semibold">Recover operation fault</Dialog.Title>
          <Dialog.Description className="my-3">{reason}</Dialog.Description>
          <p>Recovery acknowledges the failed operation and requests a fresh hardware check. It does not enable motors, move the robot, resume the turn or resume an old goal. Dock state is re-evaluated from physical charging evidence.</p>
          <p className="my-2">After recovery, release all controls and make a new RB press or dashboard manual-control request. Ordinary bumper, obstacle and hardware safeguards still apply.</p>
          <p>Dock state: {base?.dock_state ?? "Unknown"}. Motors: {base?.motors_enabled ? "enabled" : "disabled"}.</p>
          {base?.operation_fault_reason && <p className="break-all">Reason code: {base.operation_fault_reason}</p>}
          {base?.operation_fault_request_id && <p className="break-all">Operation: {base.operation_fault_request_id}</p>}
          {blocked && <p className="my-2 text-amber-300">{blocked}</p>}
          {lastResult?.command === "dock_reset" && <p className="my-2">Recovery {lastResult.outcome}: {lastResult.detail}</p>}
          <div className="mt-4 flex gap-3">
            <button disabled={!!blocked} className="rounded border px-3 py-2 disabled:opacity-40" onClick={() => send("dock_reset")}>Confirm recovery</button>
            <Dialog.Close asChild><button className="rounded border px-3 py-2">Close</button></Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </section>;
}
