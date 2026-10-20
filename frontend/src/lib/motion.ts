import { useTelemetryStore } from "../stores/telemetryStore";
import type { MotionData } from "../types/protocol";

/**
 * Independent measured-speed telemetry (`telemetry.motion`). It is derived from
 * raw `/odom` twist and is deliberately NOT `telemetry.pose`: the bridge only
 * transmits pose twist with valid AMCL/map context, so pose is not a
 * manual-drive speed source.
 *
 * The telemetry store owns the `motion`/`motionAt` fields; this adapter reads
 * them structurally so the UI compiles against older stores and honestly
 * reports "no fresh speed data" rather than a fabricated zero.
 */
export type { MotionData };

export interface MotionReadout {
  motion: MotionData | null;
  motionAt: number | null;
  transportAgeS: number | null;
}

/** Anything older than this is not safe to present as a current speed. */
export const MOTION_MAX_AGE_S = 1.5;

/** The telemetry fields may not exist yet on the store this UI is built against. */
interface MotionCarrier {
  motion?: MotionData | null;
  motionAt?: number | null;
  motionTransportAgeS?: number | null;
}

export function readMotionSnapshot(): MotionReadout {
  const state = useTelemetryStore.getState() as unknown as MotionCarrier;
  const motion = state.motion ?? null;
  return {
    motion,
    motionAt: state.motionAt ?? null,
    transportAgeS: state.motionTransportAgeS ?? null,
  };
}

/** Reactive read of the three motion fields without requiring a socket hook. */
export function useMotion(): MotionReadout {
  const motion = useTelemetryStore((state) => (state as unknown as MotionCarrier).motion ?? null);
  const motionAt = useTelemetryStore((state) => (state as unknown as MotionCarrier).motionAt ?? null);
  const transportAgeS = useTelemetryStore(
    (state) => {
      const carrier = state as unknown as MotionCarrier;
      return carrier.motionTransportAgeS ?? null;
    },
  );
  return { motion, motionAt, transportAgeS };
}

export interface MotionFreshness {
  fresh: boolean;
  /** Total worst-case age in seconds: robot sample + server receipt + transport + local. */
  ageS: number | null;
  reason: string | null;
  linear: number | null;
  angular: number | null;
  /** True when the data is internally valid but must not be shown as zero. */
  stale: boolean;
}

/**
 * Gauge age checks include the robot's own sample age, the server receipt age,
 * the envelope transport age and the local age since the browser received it.
 * Non-finite or negative ages are treated as invalid (stale), never as zero.
 * Data from an older robot session generation is dropped.
 */
export function motionFreshness(
  readout: MotionReadout,
  now: number,
  sessionGeneration: number | null,
): MotionFreshness {
  const stale = (reason: string, ageS: number | null = null): MotionFreshness => ({
    fresh: false, ageS, reason, linear: null, angular: null, stale: true,
  });
  const { motion } = readout;
  if (!motion || readout.motionAt === null) {
    return stale("No fresh speed data");
  }
  const { linear_velocity: linear, angular_velocity: angular, sample_age_s: sampleAge, receipt_age_s: receiptAge } = motion;
  const localAge = (now - readout.motionAt) / 1000;
  const transportAge = readout.transportAgeS ?? 0;
  if (![linear, angular, sampleAge, receiptAge, localAge, transportAge].every(Number.isFinite)) {
    return stale("Speed data is invalid — showing no value");
  }
  if (sampleAge < 0 || receiptAge < 0 || localAge < 0 || transportAge < 0) {
    return stale("Speed data has an invalid age — showing no value");
  }
  if (sessionGeneration === null || !Number.isFinite(sessionGeneration)) {
    return stale("Waiting for the robot session state — no speed value yet");
  }
  if (!Number.isFinite(motion.session_generation) || motion.session_generation !== sessionGeneration) {
    return stale("Speed data is from an older robot session");
  }
  const ageS = sampleAge + receiptAge + transportAge + localAge;
  if (ageS > MOTION_MAX_AGE_S) {
    return stale(`Speed data is stale (${ageS.toFixed(1)} s old)`);
  }
  return { fresh: true, ageS, reason: null, linear, angular, stale: false };
}
