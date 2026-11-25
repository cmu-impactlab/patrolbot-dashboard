import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { manualDriveReason, operationalLimits, TELEOP_LIMITS, TELEOP_TICK_MS } from "../lib/teleop";
import { useTeleopStore } from "../stores/teleopStore";
import { canCommand, useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useCommandStore } from "../stores/commandStore";

const DIRECTIONS: Record<string, [number, number]> = {
  ArrowUp: [1, 0], w: [1, 0], ArrowDown: [-1, 0], s: [-1, 0],
  ArrowLeft: [0, 1], a: [0, 1], ArrowRight: [0, -1], d: [0, -1],
};
const PHASE_COPY = { idle: "Not armed", armed: "Armed — hold to request a session", pending: "Session pending — waiting for robot acceptance", active: "Active — manual input held", stopping: "Stopping — waiting for robot result" };
type AxisLimits = typeof TELEOP_LIMITS.linear_m_s;

function scaleAxis(input: number, limits: AxisLimits, wire: AxisLimits, selected: number | null): number {
  const maximum = input < 0 ? Math.min(-limits.min, -wire.min) : Math.min(limits.max, wire.max);
  return input * Math.min(maximum, selected ?? maximum);
}

function SpeedSlider({ label, unit, minimum, maximum, selected, adjustable, onSelect }: {
  label: string; unit: string; minimum: number; maximum: number | null;
  selected: number | null; adjustable: boolean; onSelect: (value: number) => void;
}) {
  const id = useId();
  const max = maximum ?? 0;
  const min = Math.min(minimum, max);
  const value = Math.min(max, selected ?? max);
  const display = maximum === null ? "Unavailable" : `${value.toFixed(3).replace(/0$/, "")} ${unit}`;
  return <div className="manual-speed">
    <label htmlFor={id}>{label}</label>
    <span>{display}</span>
    <input id={id} type="range" min={min} max={max} step="any" value={value}
      aria-valuetext={display} disabled={!adjustable || max <= min}
      onChange={event => {
        if (!adjustable || max <= min || useTeleopStore.getState().phase !== "idle") return;
        const next = event.currentTarget.valueAsNumber;
        if (Number.isFinite(next)) onSelect(Math.max(min, Math.min(max, next)));
      }} />
  </div>;
}

function editable(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && !!target.closest("input, textarea, select, [contenteditable=true]");
}

export function ManualDrive({ compact = false }: { compact?: boolean }) {
  const user = useAuthStore(state => state.user);
  const telemetry = useTelemetryStore();
  useCommandStore();
  const state = useTeleopStore();
  const [, refresh] = useState(0);
  const [stick, setStick] = useState<[number, number]>([0, 0]);
  const [linearCap, setLinearCap] = useState<number | null>(null);
  const [angularCap, setAngularCap] = useState<number | null>(null);
  const driveSession = telemetry.baseState?.session_generation;
  const { wsConnected, robotId } = telemetry;
  const keys = useRef(new Set<string>());
  const pointer = useRef<number | null>(null);
  const blockedKeys = useRef(new Set<string>());
  const panel = useRef<HTMLElement>(null);
  const reset = () => {
    keys.current.forEach(key => blockedKeys.current.add(key));
    keys.current.clear();
    pointer.current = null;
    setStick([0, 0]);
    useTeleopStore.getState().end();
  };

  useEffect(() => {
    // Trims are input preferences for this connection, never saved robot policy.
    setLinearCap(null);
    setAngularCap(null);
  }, [wsConnected, robotId, driveSession, user?.id]);

  useEffect(() => {
    const blur = () => reset();
    const visibility = () => { if (document.visibilityState === "hidden") reset(); };
    const keyup = (event: globalThis.KeyboardEvent) => blockedKeys.current.delete(event.key.length === 1 ? event.key.toLowerCase() : event.key);
    const timer = setInterval(() => refresh(value => value + 1), TELEOP_TICK_MS);
    window.addEventListener("blur", blur);
    window.addEventListener("pagehide", blur);
    window.addEventListener("keyup", keyup);
    document.addEventListener("visibilitychange", visibility);
    const unsubscribe = useTeleopStore.subscribe(next => {
      if (!next.deadman && next.phase !== "armed") {
        keys.current.forEach(key => blockedKeys.current.add(key));
        keys.current.clear();
        pointer.current = null;
        setStick([0, 0]);
      }
    });
    return () => {
      unsubscribe();
      clearInterval(timer);
      window.removeEventListener("blur", blur);
      window.removeEventListener("pagehide", blur);
      window.removeEventListener("keyup", keyup);
      document.removeEventListener("visibilitychange", visibility);
      useTeleopStore.getState().end();
    };
  }, []);

  const reason = manualDriveReason();
  const usable = !reason && ["armed", "pending", "active"].includes(state.phase);
  const limits = operationalLimits();
  const speedAvailable = telemetry.wsConnected && telemetry.connection.state === "online"
    && telemetry.capabilities.includes("web_teleop_v1") && limits !== null;
  const canAdjustSpeed = speedAvailable && canCommand(user) && state.phase === "idle";
  const sendInput = (linear: number, angular: number, deadman: boolean, ramp: boolean) => {
    const current = operationalLimits();
    if (!current) { useTeleopStore.getState().end("Active speed policy unavailable."); return; }
    useTeleopStore.getState().input(
      scaleAxis(linear, current.linear_m_s, TELEOP_LIMITS.linear_m_s, linearCap),
      scaleAxis(angular, current.angular_rad_s, TELEOP_LIMITS.angular_rad_s, angularCap),
      deadman, false, ramp,
    );
  };
  const sendKeys = () => {
    let linear = 0, angular = 0;
    keys.current.forEach(key => { const direction = DIRECTIONS[key]; if (direction) { linear += direction[0]; angular += direction[1]; } });
    // Digital keys request a direction and ramp from zero; releasing is
    // immediate zero. Analog deflection stays proportional.
    sendInput(linear, angular, keys.current.has(" "), true);
  };
  const keyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (editable(event.target) || (event.target !== panel.current && event.target !== event.currentTarget.querySelector(".manual-joystick"))) return;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    if (key !== " " && !DIRECTIONS[key]) return;
    event.preventDefault();
    if (!usable || event.repeat || blockedKeys.current.has(key) || pointer.current !== null) return;
    keys.current.add(key);
    if (keys.current.has(" ")) sendKeys();
  };
  const keyUp = (event: KeyboardEvent<HTMLElement>) => {
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    blockedKeys.current.delete(key);
    if (editable(event.target)) return;
    keys.current.delete(key);
    if (key === " ") { event.preventDefault(); reset(); }
    else if (DIRECTIONS[key] && keys.current.has(" ")) { event.preventDefault(); sendKeys(); }
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (pointer.current !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, (event.clientX - rect.left - rect.width / 2) / (rect.width / 2)));
    const y = Math.max(-1, Math.min(1, (rect.top + rect.height / 2 - event.clientY) / (rect.height / 2)));
    setStick([x, y]);
    sendInput(y, -x, true, false);
  };

  return <section ref={panel} className={`manual-panel${compact ? " manual-compact" : ""}`} aria-label="Manual drive keyboard panel" tabIndex={0}
    onKeyDown={keyDown} onKeyUp={keyUp} onBlur={event => {
      if (useTeleopStore.getState().deadman || !event.currentTarget.contains(event.relatedTarget)) reset();
    }}>
    <h2>Manual drive</h2>
    <p role="status">{PHASE_COPY[state.phase]}</p>
    {reason && <p className="nav-disabled-banner">{reason}</p>}
    {state.detail && <p className="subtext">{state.detail}</p>}
    <p id="manual-instructions">{compact
      ? "Arm, then hold the joystick or Space + arrows/WASD. Release to end input; arm again for a new session."
      : "Arm, then hold and drag the joystick. For keyboard input, focus this panel and hold Space plus Arrow keys or WASD. Release Space or the pointer to end input. Arm again for a new session."}</p>
    <div className="manual-speeds">
      <SpeedSlider label="Linear speed limit" unit="m/s" minimum={0.05}
        maximum={speedAvailable ? Math.min(limits.linear_m_s.max, TELEOP_LIMITS.linear_m_s.max) : null}
        selected={linearCap} adjustable={canAdjustSpeed} onSelect={setLinearCap} />
      <SpeedSlider label="Angular speed limit" unit="rad/s" minimum={0.10}
        maximum={speedAvailable ? Math.min(limits.angular_rad_s.max, TELEOP_LIMITS.angular_rad_s.max) : null}
        selected={angularCap} adjustable={canAdjustSpeed} onSelect={setAngularCap} />
      <small>{state.phase === "idle" ? "Set before arming. Linear covers forward and reverse." : "End manual input to change speed."}</small>
    </div>
    <div className="manual-actions">
      <button className="btn primary" disabled={!!reason || state.phase !== "idle"} onClick={() => { state.arm(); panel.current?.focus(); }}>Arm manual drive</button>
      {state.phase === "idle" && /in control of the robot|another operator|lease/i.test(state.detail ?? "") && <button className="btn danger" disabled={!!reason} onClick={() => { state.arm(true); panel.current?.focus(); }}>Take over</button>}
      <button className="btn danger" disabled={(state.phase === "idle" && !state.externalBusy) || state.phase === "stopping" || !canCommand(user) || !telemetry.wsConnected} onClick={() => {
        if (state.phase === "idle" && state.externalBusy) useCommandStore.getState().stop();
        else reset();
      }}>End manual input</button>
    </div>
    <div className="manual-joystick" role="group" aria-label="Hold joystick to drive" aria-describedby="manual-instructions" aria-disabled={!usable} tabIndex={0}
      onPointerDown={event => {
        if (!usable || pointer.current !== null || event.button !== 0) return;
        event.preventDefault();
        keys.current.clear();
        pointer.current = event.pointerId;
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        move(event);
      }}
      onPointerMove={move}
      onPointerUp={reset} onPointerCancel={reset} onLostPointerCapture={reset}>
      <span className="manual-stick" style={{ transform: `translate(${stick[0] * 46}px, ${-stick[1] * 46}px)` }} />
      <span className="manual-forward">Forward</span>
    </div>
    <p className="subtext">No navigation resumes when manual input ends. Motors are never enabled by this page.</p>
    <p className="subtext">Ending input requests zero velocity; it does not confirm the robot stopped. In an emergency use the robot’s physical emergency-stop button, not this page.</p>
  </section>;
}
