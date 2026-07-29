/**
 * The PatrolBot's real footprint, in metres, in the ROS `base_link` frame
 * (+x forward, +y left).
 *
 * Source of truth: the octagon Nav2 actually plans and collision-checks with,
 * `local_costmap`/`global_costmap` `footprint` in
 * `patrolbot-repo/ros2_ws/src/patrolbot_navigation/config/nav2_params.yaml`,
 * which is itself taken from the robot's own ARIA parameter file
 * (`patrolbot-sh.p`):
 *
 *     RobotLength  510 mm  (±0.255 m in x, front-to-back)
 *     RobotWidth   425 mm  (±0.2125 m in y, side-to-side)
 *     corner vertices on the User's Guide swing radius of 0.29 m
 *
 * Note the robot is LONGER than it is wide. The dashboard's earlier drawings
 * used "589 x 483 mm" with those axes swapped — that figure is the User's
 * Guide overall envelope, not the chassis polygon, and drawing it rotated
 * made the robot look wider than deep. Anything that draws the robot derives
 * from the constants here so the dashboard and the planner agree.
 */

/** Octagon vertices, closed implicitly, counter-clockwise from front-right. */
export const ROBOT_FOOTPRINT_M: readonly (readonly [number, number])[] = [
  [0.255, -0.138],  // front face, right corner
  [0.255, 0.138],   // front face, left corner
  [0.197, 0.213],   // front-left diagonal -> left face
  [-0.197, 0.213],  // left face (drive wheel)
  [-0.255, 0.138],  // rear-left diagonal
  [-0.255, -0.138], // rear face
  [-0.197, -0.213], // rear-right diagonal
  [0.197, -0.213],  // right face (drive wheel)
] as const;

/** Front-to-back, metres (ARIA RobotLength 510 mm). */
export const ROBOT_LENGTH_M = 0.510;
/** Side-to-side, metres (ARIA RobotWidth 425 mm; the polygon rounds to 0.213). */
export const ROBOT_WIDTH_M = 0.426;
/** Turn-in-place circle from the User's Guide — every corner lies on it. */
export const ROBOT_SWING_RADIUS_M = 0.29;

/** Half the length of a drive wheel along the robot's x axis, metres. The
 *  wheels sit centred on the two flat side faces. */
export const WHEEL_HALF_LENGTH_M = 0.09;
/** Where the side faces are, metres from centreline. */
export const WHEEL_Y_M = 0.213;

/**
 * Trace the footprint into a canvas path, in metres scaled by `pxPerM`.
 *
 * Expects the context already translated to the robot's screen position and
 * rotated by `-yaw` (canvas y grows downward, so the y term is negated here
 * and the caller's rotation is negated to match).
 */
export function traceFootprint(
  ctx: CanvasRenderingContext2D,
  pxPerM: number,
): void {
  ctx.beginPath();
  ROBOT_FOOTPRINT_M.forEach(([x, y], index) => {
    const sx = x * pxPerM;
    const sy = -y * pxPerM;
    if (index === 0) ctx.moveTo(sx, sy);
    else ctx.lineTo(sx, sy);
  });
  ctx.closePath();
}
