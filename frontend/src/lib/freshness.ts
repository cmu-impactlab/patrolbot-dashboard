import { useEffect, useState } from "react";

/**
 * How old a drive-base frame may be before the widgets stop speaking for it.
 * Matches MAX_BASE_STATE_AGE_S in server/app/telemetry/status.py: the health
 * card and the widgets must not disagree about whether the robot is reporting.
 */
export const MAX_BASE_STATE_AGE_MS = 15_000;
/** Matches MAX_BATTERY_AGE_S in server/app/telemetry/status.py. */
export const MAX_BATTERY_AGE_MS = 30_000;
/** Matches MAX_RESOURCES_AGE_S in server/app/telemetry/status.py. */
export const MAX_RESOURCES_AGE_MS = 60_000;

/**
 * Whether `at` is recent enough to still describe the robot. `at` is a
 * `performance.now()` reading — monotonic since page load, so a system clock
 * adjustment cannot make stale telemetry look fresh.
 *
 * Re-renders on a timer, because staleness arrives with the passage of time
 * rather than with a frame: nothing pushes an update when the robot goes quiet,
 * which is exactly the case that needs to change what is on screen.
 */
export function useIsFresh(at: number | null, limitMs = MAX_BASE_STATE_AGE_MS): boolean {
  const [fresh, setFresh] = useState(() => at !== null && performance.now() - at <= limitMs);

  useEffect(() => {
    const check = () => setFresh(at !== null && performance.now() - at <= limitMs);
    check();
    const timer = setInterval(check, 1000);
    return () => clearInterval(timer);
  }, [at, limitMs]);

  return fresh;
}
