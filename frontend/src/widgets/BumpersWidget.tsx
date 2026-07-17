import { useTelemetryStore } from "../stores/telemetryStore";

/**
 * Top-down view of the PatrolBot matching the real chassis: a rectangular
 * body with a segmented bumper strip wrapped across the front face and
 * another across the rear, drive wheels mid-body. The hardware reports each
 * strip as a whole (pressed / not pressed), so all segments of a strip
 * light together when it is hit.
 */
function BumperStrip({ pressed, y, flip }: { pressed: boolean; y: number; flip?: boolean }) {
  const fill = pressed ? "var(--danger)" : "var(--muted-bg)";
  const stroke = pressed ? "var(--danger)" : "var(--border)";
  // Three visible segments, gently curved like the physical strip.
  const curve = flip ? 6 : -6;
  const segments = [
    { x1: 28, x2: 62 },
    { x1: 66, x2: 114 },
    { x1: 118, x2: 152 },
  ];
  return (
    <g className={pressed ? "bumper-hit" : ""}>
      {segments.map((segment, index) => (
        <path
          key={index}
          d={`M ${segment.x1} ${y} Q ${(segment.x1 + segment.x2) / 2} ${y + curve} ${segment.x2} ${y}
              l 0 ${flip ? -9 : 9} Q ${(segment.x1 + segment.x2) / 2} ${y + curve + (flip ? -9 : 9)} ${segment.x1} ${y + (flip ? -9 : 9)} Z`}
          fill={fill}
          stroke={stroke}
          strokeWidth="1.5"
        />
      ))}
    </g>
  );
}

export function BumpersWidget() {
  const baseState = useTelemetryStore((state) => state.baseState);
  const front = baseState?.bumpers_front ?? false;
  const rear = baseState?.bumpers_rear ?? false;

  return (
    <div className="bumpers-widget">
      <svg viewBox="0 0 180 260" className="bumpers-svg" role="img"
           aria-label="Robot bumper diagram (top view)">
        <text x="90" y="14" textAnchor="middle" className="bumper-label">FRONT</text>

        <BumperStrip pressed={front} y={30} />

        {/* Chassis body */}
        <rect x="34" y="44" width="112" height="172" rx="14"
              fill="var(--surface-2)" stroke="var(--border)" strokeWidth="2" />
        {/* Drive wheels mid-body */}
        <rect x="24" y="108" width="12" height="44" rx="5" fill="var(--muted)" />
        <rect x="144" y="108" width="12" height="44" rx="5" fill="var(--muted)" />
        {/* Heading marker */}
        <path d="M 90 66 l 12 22 h -24 Z" fill="var(--cmu-red)" />

        <BumperStrip pressed={rear} y={230} flip />

        <text x="90" y="252" textAnchor="middle" className="bumper-label">REAR</text>
      </svg>

      <div className="kv-list">
        <div className="kv">
          <span className="k">Front bumper</span>
          <span className="v" style={{ color: front ? "var(--danger)" : undefined }}>
            {baseState ? (front ? "PRESSED" : "Clear") : "—"}
          </span>
        </div>
        <div className="kv">
          <span className="k">Rear bumper</span>
          <span className="v" style={{ color: rear ? "var(--danger)" : undefined }}>
            {baseState ? (rear ? "PRESSED" : "Clear") : "—"}
          </span>
        </div>
      </div>
      {(front || rear) && (
        <p className="subtext" style={{ color: "var(--danger)", fontWeight: 600 }}>
          The robot touched something. Check that its path is clear before continuing.
        </p>
      )}
      <p className="subtext">
        The hardware reports each strip as a whole, so all segments of a hit
        strip light together.
      </p>
    </div>
  );
}
