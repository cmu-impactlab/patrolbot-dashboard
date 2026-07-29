import { useTelemetryStore } from "../stores/telemetryStore";
import {
  ROBOT_FOOTPRINT_M, ROBOT_LENGTH_M, WHEEL_HALF_LENGTH_M, WHEEL_Y_M,
} from "../lib/robotGeometry";

/**
 * Top-down view of the PatrolBot at its true proportions: the octagonal
 * 510 x 426 mm footprint from lib/robotGeometry (the polygon Nav2 plans
 * with, out of the robot's own ARIA params), whose perimeter facets are the
 * segmented bumper panels — three across the front (left diagonal, front
 * face, right diagonal), three across the rear, with the drive wheels on the
 * flat side faces. The hardware reports each group as a whole, so all three
 * panels of a hit group light together.
 *
 * The robot is longer than it is wide; an earlier version of this drawing had
 * those axes the other way round.
 */

const CX = 100;
const CY = 120;
// FRONT is up, so the robot's +x (forward) maps to -y on screen and its +y
// (left) maps to -x. Scale set so the 510 mm length fills 150 px.
const PX_PER_M = 150 / ROBOT_LENGTH_M;

function project([x, y]: readonly [number, number]): readonly [number, number] {
  return [-y * PX_PER_M, -x * PX_PER_M];
}

const [frontR, frontL, sideLT, sideLB, rearL, rearR, sideRB, sideRT] =
  ROBOT_FOOTPRINT_M.map(project);

const P = { frontL, frontR, sideRT, sideRB, rearR, rearL, sideLB, sideLT } as const;

function pt([x, y]: readonly [number, number], scale = 1): string {
  return `${CX + x * scale} ${CY + y * scale}`;
}

// Drive wheels straddle the two flat side faces, centred front-to-back.
const WHEEL_X = WHEEL_Y_M * PX_PER_M;
const WHEEL_HALF_LEN = WHEEL_HALF_LENGTH_M * PX_PER_M;
const WHEEL_THICKNESS = 9;
// Laser window sits just inside the front face.
const FRONT_Y = P.frontL[1];
const LASER_Y = FRONT_Y + 7;
const LASER_HALF = Math.abs(P.frontL[0]) * 0.85;

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

        {/* Octagonal chassis (510 mm front-to-back x 426 mm across, FRONT up) */}
        <path d={BODY} fill="var(--surface-2)" stroke="var(--border)" strokeWidth="2" />

        {/* Drive wheels straddling the flat side faces */}
        <rect x={CX - WHEEL_X - WHEEL_THICKNESS / 2} y={CY - WHEEL_HALF_LEN}
              width={WHEEL_THICKNESS} height={WHEEL_HALF_LEN * 2} rx="4" fill="var(--muted)" />
        <rect x={CX + WHEEL_X - WHEEL_THICKNESS / 2} y={CY - WHEEL_HALF_LEN}
              width={WHEEL_THICKNESS} height={WHEEL_HALF_LEN * 2} rx="4" fill="var(--muted)" />

        {/* Laser window across the front and heading wedge */}
        <path d={`M ${pt([-LASER_HALF, LASER_Y])} L ${pt([LASER_HALF, LASER_Y])}`}
              stroke="var(--info)" strokeWidth="4" strokeLinecap="round" opacity="0.6" />
        <path d={`M ${pt([0, FRONT_Y * 0.45])} L ${pt([13, -8])} L ${pt([-13, -8])} Z`}
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
