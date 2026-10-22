import { useEffect, useState } from "react";
import { useTelemetryStore } from "../stores/telemetryStore";
import { motionFreshness, useMotion } from "../lib/motion";
import { gaugeReading } from "../lib/tuning";
import type { GaugeReading } from "../lib/tuning";

export interface EffectiveLimits {
  max_linear_m_s: number;
  max_angular_rad_s: number;
}

const R = 64;
const CX = 80;
const CY = 88;

/** Point on the gauge dial for a needle angle (degrees, 180 = left, 0 = right). */
function point(deg: number): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [CX + R * Math.cos(rad), CY - R * Math.sin(rad)];
}

/** Arc from `startDeg` over the top toward `endDeg` (both on the dial). */
function arc(startDeg: number, endDeg: number): string {
  const [x0, y0] = point(startDeg);
  const [x1, y1] = point(endDeg);
  const sweep = endDeg < startDeg ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 0 ${sweep} ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

export function capMarkerAngles(cap: number | null, scale: number): number[] {
  if (cap === null || !Number.isFinite(cap) || cap < 0 || cap > scale) return [];
  const angle = (cap / scale) * 90;
  return angle === 0 ? [90] : [90 + angle, 90 - angle];
}

function Dial({ reading, scale, unit }: { reading: GaugeReading; scale: number; unit: string }) {
  const cap = reading.cap;
  // A fixed, labelled hard-envelope scale lets a lower runtime cap have a
  // meaningful red marker. The hard envelope is NOT used as a cap fallback.
  const fraction = reading.value !== null ? Math.max(-1, Math.min(1, reading.value / scale)) : 0;
  const needleDeg = fraction * 90;
  const valueEnd = 90 - fraction * 90;
  const needleKnown = reading.value !== null;
  const markers = capMarkerAngles(cap, scale);
  return (
    <svg className="gauge-dial" viewBox="0 0 160 114" aria-hidden="true" focusable="false">
      <path className="gauge-track" d={arc(180, 0)} />
      {reading.value !== null && reading.band !== "unknown" && (
        <path className={`gauge-value gauge-${reading.band}`} d={arc(90, valueEnd)} />
      )}
      {markers.length > 0 && <g className="gauge-cap-markers" data-cap={cap}>
        <title>Live speed cap ±{cap?.toFixed(2)} {unit}</title>
        {markers.map(angle => {
          const rad = angle * Math.PI / 180;
          return <line key={angle} className="gauge-cap-marker" data-angle={angle}
            x1={CX + (R - 10) * Math.cos(rad)} y1={CY - (R - 10) * Math.sin(rad)}
            x2={CX + (R + 7) * Math.cos(rad)} y2={CY - (R + 7) * Math.sin(rad)} />;
        })}
      </g>}
      {needleKnown && <g className="gauge-needle" transform={`rotate(${needleDeg.toFixed(1)} ${CX} ${CY})`}>
        <line x1={CX} y1={CY} x2={CX} y2={CY - R + 10} />
      </g>}
      <circle className="gauge-hub" cx={CX} cy={CY} r={4} />
      <g className="gauge-scale"><text x={8} y={108}>−{scale.toFixed(2)}</text>
        <text x={80} y={108} textAnchor="middle">0</text>
        <text x={152} y={108} textAnchor="end">+{scale.toFixed(2)}</text></g>
    </svg>
  );
}

function Gauge({ title, kind, reading, unit, staleReason, fresh }: {
  title: string;
  kind: "linear" | "angular";
  reading: GaugeReading;
  unit: string;
  fresh: boolean;
  staleReason: string | null;
}) {
  const value = reading.value;
  const signed = value !== null ? `${value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}` : "—";
  const capText = reading.cap === null ? "active cap unknown" : `active cap ${reading.cap.toFixed(2)} ${unit}`;
  const percent = reading.percent !== null ? `${Math.round(reading.percent)}% of cap` : null;
  const valueText = fresh && value !== null
    ? `${signed} ${unit}, ${reading.direction}, ${percent ?? "cap unknown"}`
    : staleReason ?? "No fresh speed data";
  return (
    <figure className={`gauge gauge-${reading.band}${fresh ? "" : " gauge-stale"}`}>
      <figcaption className="gauge-title">{title}</figcaption>
      <Dial reading={reading} scale={kind === "linear" ? .30 : .70} unit={unit} />
      <div className="gauge-readout" role="meter" aria-label={`Measured ${kind} speed`}
        aria-valuemin={reading.cap !== null ? -Math.abs(reading.cap) : undefined}
        aria-valuemax={reading.cap !== null ? Math.abs(reading.cap) : undefined}
        aria-valuenow={fresh && value !== null ? value : undefined}
        aria-valuetext={valueText}>
        {fresh && value !== null ? (
          <>
            <span className="gauge-value-text">{signed} <span className="gauge-unit">{unit}</span></span>
            <span className="gauge-direction">{reading.direction}</span>
            <span className="gauge-cap">{capText}</span>
            {percent && <span className="gauge-percent">{percent}</span>}
            {reading.overLimit && <span className="gauge-over" role="alert" aria-label="Over the active cap">Over the active cap</span>}
          </>
        ) : (
          <>
            <span className="gauge-value-text gauge-none">No fresh speed data</span>
            <span className="gauge-cap">{capText}</span>
            {staleReason && <span className="gauge-reason">{staleReason}</span>}
          </>
        )}
      </div>
    </figure>
  );
}

/**
 * Two compact measured-speed gauges. They read ONLY the independent
 * `telemetry.motion` slice, never `telemetry.pose`, and never fabricate a zero
 * when the odometry is missing or stale. Threshold bands are on the measured
 * magnitude relative to the ACTIVE effective cap reported by the robot.
 */
export function SpeedGauges({ limits, limitsAt }: { limits: EffectiveLimits | null; limitsAt?: number | null }) {
  const { motion, motionAt, transportAgeS } = useMotion();
  const generation = useTelemetryStore((state) => state.baseState?.session_generation ?? null);
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(performance.now()), 500);
    return () => clearInterval(timer);
  }, []);
  // Motion arrives more often than the aging timer. A cached timer timestamp
  // can precede a newly received sample and incorrectly make its age negative.
  const checkedAt = Math.max(now, performance.now());
  const freshness = motionFreshness({ motion, motionAt, transportAgeS }, checkedAt, generation);
  const freshLimits = limitsAt === undefined || (limitsAt !== null
    && Number.isFinite(limitsAt) && checkedAt >= limitsAt && checkedAt - limitsAt <= 3000);
  const linear = gaugeReading(freshness.fresh ? freshness.linear : null, freshLimits ? limits?.max_linear_m_s ?? null : null, "linear");
  const angular = gaugeReading(freshness.fresh ? freshness.angular : null, freshLimits ? limits?.max_angular_rad_s ?? null : null, "angular");
  return (
    <section className="speed-gauges" aria-label="Measured speed gauges">
      <Gauge title="Linear speed" kind="linear" reading={linear} unit="m/s"
        fresh={freshness.fresh} staleReason={freshness.reason} />
      <Gauge title="Angular speed" kind="angular" reading={angular} unit="rad/s"
        fresh={freshness.fresh} staleReason={freshness.reason} />
      <p className="speed-gauges-note"><span className="cap-legend" />Red marks: live cap, not a pending target. Raw wheel odometry; no localization required.</p>
    </section>
  );
}
