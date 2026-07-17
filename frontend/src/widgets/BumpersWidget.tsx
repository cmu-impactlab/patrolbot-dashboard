import { useTelemetryStore } from "../stores/telemetryStore";

/**
 * Top-down view of the PatrolBot matching the manual's dimension drawing
 * (User's Guide Fig. 8-1): an octagonal footprint 589 mm wide x 483 mm
 * deep whose perimeter facets are the segmented bumper panels — three
 * across the front (left diagonal, front face, right diagonal), three
 * across the rear, with the drive wheels on the flat side faces. The
 * hardware reports each group as a whole, so all three panels of a hit
 * group light together.
 */

// Octagon centered at (100, 120); half-width 82, half-height 66 keeps the
// manual's 589:483 aspect ratio. FRONT is up.
const CX = 100;
const CY = 120;
const P = {
  frontL: [-38, -66], frontR: [38, -66],   // front face
  sideRT: [82, -22], sideRB: [82, 22],     // right face (wheel)
  rearR: [38, 66], rearL: [-38, 66],       // rear face
  sideLB: [-82, 22], sideLT: [-82, -22],   // left face (wheel)
} as const;

function pt([x, y]: readonly [number, number], scale = 1): string {
  return `${CX + x * scale} ${CY + y * scale}`;
}

const BODY = `M ${pt(P.frontL)} L ${pt(P.frontR)} L ${pt(P.sideRT)} L ${pt(P.sideRB)} ` +
  `L ${pt(P.rearR)} L ${pt(P.rearL)} L ${pt(P.sideLB)} L ${pt(P.sideLT)} Z`;

// Bumper panels: the three front facets and three rear facets, drawn as
// thick strips slightly outside the body outline.
const PANEL_SCALE = 1.12;
const FRONT_PANELS = [
  [P.sideLT, P.frontL],
  [P.frontL, P.frontR],
  [P.frontR, P.sideRT],
] as const;
const REAR_PANELS = [
  [P.sideRB, P.rearR],
  [P.rearR, P.rearL],
  [P.rearL, P.sideLB],
] as const;

function PanelGroup({ panels, pressed }: {
  panels: typeof FRONT_PANELS | typeof REAR_PANELS;
  pressed: boolean;
}) {
  return (
    <g className={pressed ? "bumper-hit" : ""}>
      {panels.map(([a, b], index) => (
        <path
          key={index}
          d={`M ${pt(a, PANEL_SCALE)} L ${pt(b, PANEL_SCALE)}`}
          fill="none"
          stroke={pressed ? "var(--danger)" : "var(--muted-bg)"}
          strokeWidth="9"
          strokeLinecap="round"
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
      <svg viewBox="0 0 200 240" className="bumpers-svg" role="img"
           aria-label="Robot bumper diagram (top view)">
        <text x="100" y="18" textAnchor="middle" className="bumper-label">FRONT</text>

        <PanelGroup panels={FRONT_PANELS} pressed={front} />
        <PanelGroup panels={REAR_PANELS} pressed={rear} />

        {/* Octagonal chassis (589 x 483 mm footprint, FRONT up) */}
        <path d={BODY} fill="var(--surface-2)" stroke="var(--border)" strokeWidth="2" />

        {/* Drive wheels on the flat side faces */}
        <rect x="12" y="98" width="9" height="44" rx="4" fill="var(--muted)" />
        <rect x="179" y="98" width="9" height="44" rx="4" fill="var(--muted)" />

        {/* Laser window across the front and heading wedge */}
        <path d={`M ${pt([-30, -58])} L ${pt([30, -58])}`} stroke="var(--info)"
              strokeWidth="4" strokeLinecap="round" opacity="0.6" />
        <path d={`M ${pt([0, -34])} L ${pt([13, -8])} L ${pt([-13, -8])} Z`}
              fill="var(--cmu-red)" />

        <text x="100" y="230" textAnchor="middle" className="bumper-label">REAR</text>
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
        The hardware reports each bumper group as a whole, so all panels of a
        hit group light together.
      </p>
    </div>
  );
}
