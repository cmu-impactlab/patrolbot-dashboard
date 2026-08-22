import { useTelemetryStore } from "../stores/telemetryStore";
import { useIsFresh } from "../lib/freshness";
import {
  ROBOT_FOOTPRINT_M, ROBOT_LENGTH_M, WHEEL_HALF_LENGTH_M, WHEEL_Y_M,
} from "../lib/robotGeometry";

/**
 * Top-down view of the PatrolBot at its true proportions: the octagonal
 * 510 x 426 mm footprint from lib/robotGeometry (the polygon Nav2 plans
 * with, out of the robot's own ARIA params), with a continuous bumper bar
 * following the three front facets and another following the three rear
 * facets. The hardware reports each group as a whole, so each complete bar
 * changes state together.
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

// Bumper bars follow the three front facets and three rear facets as one
// continuous stroke per hardware-reported group, slightly outside the body.
const PANEL_SCALE = 1.12;
const FRONT_BAR = [P.sideLT, P.frontL, P.frontR, P.sideRT] as const;
const REAR_BAR = [P.sideRB, P.rearR, P.rearL, P.sideLB] as const;

function BumperBar({ points, position, pressed, known }: {
  points: typeof FRONT_BAR | typeof REAR_BAR;
  position: "front" | "rear";
  pressed: boolean;
  known: boolean;
}) {
  const state = !known ? "unknown" : pressed ? "pressed" : "clear";
  const d = points.map((point, index) =>
    `${index === 0 ? "M" : "L"} ${pt(point, PANEL_SCALE)}`).join(" ");

  // Unknown stays visibly subdued, but remains the same physical bar shape.
  // Text below the diagram carries the explicit Unknown/Clear distinction.
  return (
    <path
      data-bumper={position}
      data-state={state}
      className={known && pressed ? "bumper-hit" : undefined}
      d={d}
      fill="none"
      stroke={!known ? "var(--text-faint)" : pressed ? "var(--danger)" : "var(--muted-bg)"}
      strokeWidth="9"
      strokeLinecap="round"
      strokeLinejoin="round"
      opacity={known ? undefined : 0.45}
    />
  );
}

export function BumpersWidget() {
  const baseState = useTelemetryStore((state) => state.baseState);
  const fresh = useIsFresh(useTelemetryStore((state) => state.baseStateAt));
  // Two ways to not know: the robot says the readings are meaningless, or it
  // has stopped sending them. "Clear" is an all-clear from a safety sensor and
  // needs the robot to be currently saying so — an explicit bumpers_valid on a
  // frame that arrived recently. Silence is not consent.
  const known = baseState != null && baseState.bumpers_valid === true && fresh;
  const front = known && (baseState?.bumpers_front ?? false);
  const rear = known && (baseState?.bumpers_rear ?? false);

  return (
    <div className="bumpers-widget">
      <svg viewBox="0 0 200 240" className="bumpers-svg" role="img"
           aria-label="Robot bumper diagram (top view)">
        <text x="100" y="18" textAnchor="middle" className="bumper-label">FRONT</text>

        <BumperBar points={FRONT_BAR} position="front" pressed={front} known={known} />
        <BumperBar points={REAR_BAR} position="rear" pressed={rear} known={known} />

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
            {!baseState ? "—" : !known ? "Unknown" : front ? "PRESSED" : "Clear"}
          </span>
        </div>
        <div className="kv">
          <span className="k">Rear bumper</span>
          <span className="v" style={{ color: rear ? "var(--danger)" : undefined }}>
            {!baseState ? "—" : !known ? "Unknown" : rear ? "PRESSED" : "Clear"}
          </span>
        </div>
      </div>
      {baseState && !known && (
        <p className="subtext">
          The dashboard cannot confirm the robot's bumper readings right now,
          so it does not know whether anything is touching them. Check around
          the robot yourself before moving it.
        </p>
      )}
      {(front || rear) && (
        <p className="subtext" style={{ color: "var(--danger)", fontWeight: 600 }}>
          The robot touched something. Check that its path is clear before continuing.
        </p>
      )}
      <p className="subtext">
        The hardware reports each bumper group as a whole, so the entire front
        or rear bar changes state together.
      </p>
    </div>
  );
}
