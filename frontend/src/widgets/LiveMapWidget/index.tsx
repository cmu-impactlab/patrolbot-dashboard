import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, Crosshair, Layers, Maximize, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMapQuery } from "../../api/queries";
import { useCommandStore } from "../../stores/commandStore";
import { useReplayStore } from "../../stores/replayStore";
import { useTelemetryStore } from "../../stores/telemetryStore";
import { useUiStore, type MapLayers } from "../../stores/uiStore";
import type { MapData } from "../../types/protocol";
import { buildMapBitmap } from "./bitmap";
import { fitView, followView, screenToWorld, worldToScreen, zoomAt, type View } from "./transform";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Longest the orientation arrow can stretch on screen. */
const MAX_ARROW_PX = 80;

/** Semi-transparent top-down robot drawn under the pointer while picking a
 *  pose — the operator places "the robot" rather than an abstract cursor.
 *  Matches the PatrolBot's real octagonal footprint (589 x 483 mm, manual
 *  Fig. 8-1), like the Bumpers widget. `angle` is the screen-space heading
 *  (radians); the front faces along +x before rotation. */
function drawRobotGhost(ctx: CanvasRenderingContext2D, x: number, y: number, angle: number): void {
  const s = 15; // half-length front-to-back; width is wider (589:483)
  const w = s * 1.22;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.globalAlpha = 0.8;
  // Octagon with the front facet at +x: corners follow the manual's drawing.
  ctx.beginPath();
  ctx.moveTo(s, -w * 0.46);        // front face, left corner
  ctx.lineTo(s, w * 0.46);         // front face, right corner
  ctx.lineTo(s * 0.33, w);         // front-right diagonal -> right face
  ctx.lineTo(-s * 0.33, w);        // right face (wheel side)
  ctx.lineTo(-s, w * 0.46);        // rear-right diagonal
  ctx.lineTo(-s, -w * 0.46);       // rear face
  ctx.lineTo(-s * 0.33, -w);       // rear-left diagonal -> left face
  ctx.lineTo(s * 0.33, -w);        // left face (wheel side)
  ctx.closePath();
  ctx.fillStyle = cssVar("--surface-2");
  ctx.strokeStyle = cssVar("--muted");
  ctx.lineWidth = 1.6;
  ctx.fill();
  ctx.stroke();
  // Drive wheels on the flat side faces
  ctx.fillStyle = cssVar("--muted");
  ctx.fillRect(-s * 0.3, -w - 1.5, s * 0.6, 3);
  ctx.fillRect(-s * 0.3, w - 1.5, s * 0.6, 3);
  // Heading wedge toward the front
  ctx.beginPath();
  ctx.moveTo(s * 0.8, 0);
  ctx.lineTo(s * 0.1, w * 0.4);
  ctx.lineTo(s * 0.1, -w * 0.4);
  ctx.closePath();
  ctx.fillStyle = cssVar("--cmu-red");
  ctx.fill();
  ctx.restore();
}

interface PickArrow {
  ax: number;
  ay: number;
  ex: number;
  ey: number;
  mode: "goal" | "initialpose";
}

/** RViz-style orientation arrow drawn while the user drags a pose pick. */
function drawPickArrow(ctx: CanvasRenderingContext2D, pick: PickArrow): void {
  const color = pick.mode === "goal" ? cssVar("--info") : cssVar("--ok");
  const angle = Math.atan2(pick.ey - pick.ay, pick.ex - pick.ax);
  const length = Math.hypot(pick.ex - pick.ax, pick.ey - pick.ay);

  ctx.beginPath();
  ctx.arc(pick.ax, pick.ay, 4, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  if (length < 8) return; // no direction chosen yet

  ctx.beginPath();
  ctx.moveTo(pick.ax, pick.ay);
  ctx.lineTo(pick.ex, pick.ey);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.stroke();
  const head = 11;
  ctx.beginPath();
  ctx.moveTo(pick.ex, pick.ey);
  ctx.lineTo(pick.ex - head * Math.cos(angle - 0.45), pick.ey - head * Math.sin(angle - 0.45));
  ctx.lineTo(pick.ex - head * Math.cos(angle + 0.45), pick.ey - head * Math.sin(angle + 0.45));
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function drawScene(
  ctx: CanvasRenderingContext2D,
  view: View,
  map: MapData,
  bitmap: HTMLCanvasElement,
  layers: MapLayers,
  pickArrow: PickArrow | null,
  pickHover: [number, number] | null,
): void {
  const canvasWidth = ctx.canvas.clientWidth;
  const canvasHeight = ctx.canvas.clientHeight;
  const state = useTelemetryStore.getState();

  ctx.clearRect(0, 0, canvasWidth, canvasHeight);

  // Occupancy bitmap (pre-flipped): top-left = (origin.x, origin.y + h).
  const [mapLeft, mapTop] = worldToScreen(view, map.origin.x, map.origin.y + map.height * map.resolution);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(
    bitmap,
    mapLeft,
    mapTop,
    map.width * map.resolution * view.zoom,
    map.height * map.resolution * view.zoom,
  );

  // Traveled trajectory
  if (layers.trajectory && state.trajectory.length > 1) {
    ctx.beginPath();
    for (let i = 0; i < state.trajectory.length; i++) {
      const [sx, sy] = worldToScreen(view, state.trajectory[i][0], state.trajectory[i][1]);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.strokeStyle = cssVar("--muted");
    ctx.lineWidth = 1.4;
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Planned path
  const path = state.path;
  if (layers.plannedPath && path && path.points.length > 1) {
    ctx.beginPath();
    for (let i = 0; i < path.points.length; i++) {
      const [sx, sy] = worldToScreen(view, path.points[i][0], path.points[i][1]);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.strokeStyle = cssVar("--info");
    ctx.lineWidth = 2.2;
    ctx.stroke();
  }

  // Goal marker
  if (layers.goal && path?.goal) {
    const [gx, gy] = worldToScreen(view, path.goal.x, path.goal.y);
    ctx.beginPath();
    ctx.arc(gx, gy, 7, 0, Math.PI * 2);
    ctx.strokeStyle = cssVar("--info");
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(gx, gy, 2.2, 0, Math.PI * 2);
    ctx.fillStyle = cssVar("--info");
    ctx.fill();
  }

  // Recording replay overlay: full route (muted), progress, and a ghost
  // marker at the playhead. Distinct from the live robot (blue outline).
  const replay = useReplayStore.getState();
  if (replay.recording && replay.poses.length > 1) {
    const t = replay.now();
    ctx.beginPath();
    for (let i = 0; i < replay.poses.length; i++) {
      const [sx, sy] = worldToScreen(view, replay.poses[i].x, replay.poses[i].y);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.strokeStyle = cssVar("--muted");
    ctx.lineWidth = 1.2;
    ctx.setLineDash([2, 4]);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    for (let i = 0; i < replay.poses.length && replay.poses[i].tMs <= t; i++) {
      const [sx, sy] = worldToScreen(view, replay.poses[i].x, replay.poses[i].y);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.strokeStyle = cssVar("--active");
    ctx.lineWidth = 2.4;
    ctx.stroke();

    const ghost = replay.poseAt(t);
    if (ghost) {
      const [gx, gy] = worldToScreen(view, ghost.x, ghost.y);
      const size = Math.max(7, Math.min(16, 0.32 * view.zoom));
      ctx.save();
      ctx.translate(gx, gy);
      ctx.rotate(-ghost.yaw);
      ctx.beginPath();
      ctx.moveTo(size * 1.4, 0);
      ctx.lineTo(-size * 0.8, size * 0.75);
      ctx.lineTo(-size * 0.8, -size * 0.75);
      ctx.closePath();
      ctx.strokeStyle = cssVar("--active");
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.restore();
    }
  }

  const pose = state.pose;

  // LiDAR points (polar -> world using the latest pose)
  if (layers.lidar && pose && state.lidar) {
    const { angle_min, angle_increment, ranges } = state.lidar;
    ctx.fillStyle = cssVar("--danger");
    for (let i = 0; i < ranges.length; i++) {
      const range = ranges[i];
      if (range == null) continue;
      const angle = pose.yaw + angle_min + i * angle_increment;
      const [sx, sy] = worldToScreen(
        view,
        pose.x + range * Math.cos(angle),
        pose.y + range * Math.sin(angle),
      );
      ctx.fillRect(sx - 1, sy - 1, 2.4, 2.4);
    }
  }

  // Robot marker: CMU-red triangle + heading + uncertainty ring
  if (pose) {
    const [rx, ry] = worldToScreen(view, pose.x, pose.y);
    if (pose.localized === false || (pose.covariance_trace ?? 0) > 0.25) {
      ctx.beginPath();
      ctx.arc(rx, ry, Math.max(14, 0.6 * view.zoom), 0, Math.PI * 2);
      ctx.fillStyle = "rgba(196, 18, 48, 0.12)";
      ctx.fill();
    }
    const size = Math.max(7, Math.min(16, 0.32 * view.zoom));
    ctx.save();
    ctx.translate(rx, ry);
    ctx.rotate(-pose.yaw); // screen y is flipped
    ctx.beginPath();
    ctx.moveTo(size * 1.4, 0);
    ctx.lineTo(-size * 0.8, size * 0.75);
    ctx.lineTo(-size * 0.8, -size * 0.75);
    ctx.closePath();
    ctx.fillStyle = cssVar("--cmu-red");
    ctx.fill();
    ctx.strokeStyle = cssVar("--surface");
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.restore();
  }

  if (pickArrow) {
    const angle = Math.hypot(pickArrow.ex - pickArrow.ax, pickArrow.ey - pickArrow.ay) > 8
      ? Math.atan2(pickArrow.ey - pickArrow.ay, pickArrow.ex - pickArrow.ax)
      : 0;
    drawRobotGhost(ctx, pickArrow.ax, pickArrow.ay, angle);
    drawPickArrow(ctx, pickArrow);
  } else if (pickHover) {
    drawRobotGhost(ctx, pickHover[0], pickHover[1], 0);
  }
}

export function LiveMapWidget() {
  const mapVersion = useTelemetryStore((state) => state.mapVersion);
  const mapQuery = useMapQuery(mapVersion);
  const followRobot = useUiStore((state) => state.followRobot);
  const setFollowRobot = useUiStore((state) => state.setFollowRobot);
  const theme = useUiStore((state) => state.theme);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View | null>(null);
  const bitmapRef = useRef<{ key: string; bitmap: HTMLCanvasElement } | null>(null);
  const [panning, setPanning] = useState(false);
  const dragRef = useRef<{ sx: number; sy: number; panX: number; panY: number } | null>(null);
  const pickArrowRef = useRef<{ ax: number; ay: number; ex: number; ey: number; mode: "goal" | "initialpose" } | null>(null);
  const pickHoverRef = useRef<[number, number] | null>(null);
  const pickMode = useCommandStore((state) => state.pickMode);

  const map = mapQuery.data ?? null;

  // Render loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !map) return;
    const ctx = canvas.getContext("2d")!;
    let raf = 0;

    const render = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr;
        canvas.height = height * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const bitmapKey = `${map.map_version}:${theme}`;
      if (!bitmapRef.current || bitmapRef.current.key !== bitmapKey) {
        bitmapRef.current = { key: bitmapKey, bitmap: buildMapBitmap(map) };
      }
      if (!viewRef.current) {
        viewRef.current = fitView(map, width, height);
      }
      if (useUiStore.getState().followRobot) {
        const pose = useTelemetryStore.getState().pose;
        if (pose) viewRef.current = followView(viewRef.current, width, height, pose.x, pose.y);
      }
      drawScene(ctx, viewRef.current, map, bitmapRef.current.bitmap, useUiStore.getState().mapLayers,
                pickArrowRef.current, pickHoverRef.current);
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => cancelAnimationFrame(raf);
  }, [map, theme]);

  // Interactions
  const onWheel = (event: React.WheelEvent) => {
    if (!viewRef.current) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    viewRef.current = zoomAt(viewRef.current, event.clientX - rect.left, event.clientY - rect.top, factor);
  };

  const onPointerDown = (event: React.PointerEvent) => {
    if (!viewRef.current) return;
    (event.target as Element).setPointerCapture(event.pointerId);
    const mode = useCommandStore.getState().pickMode;
    if (mode !== "none") {
      // RViz-style pose picking: the press anchors the position; dragging
      // stretches an arrow whose direction becomes the orientation.
      const rect = canvasRef.current!.getBoundingClientRect();
      const ax = event.clientX - rect.left;
      const ay = event.clientY - rect.top;
      pickArrowRef.current = { ax, ay, ex: ax, ey: ay, mode };
      return;
    }
    dragRef.current = {
      sx: event.clientX,
      sy: event.clientY,
      panX: viewRef.current.panX,
      panY: viewRef.current.panY,
    };
    setPanning(true);
    setFollowRobot(false);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (pickArrowRef.current) {
      const rect = canvasRef.current!.getBoundingClientRect();
      const pick = pickArrowRef.current;
      // The arrow only encodes direction — cap its length so it stays a
      // compass needle instead of stretching across the map.
      let dx = event.clientX - rect.left - pick.ax;
      let dy = event.clientY - rect.top - pick.ay;
      const length = Math.hypot(dx, dy);
      if (length > MAX_ARROW_PX) {
        dx *= MAX_ARROW_PX / length;
        dy *= MAX_ARROW_PX / length;
      }
      pick.ex = pick.ax + dx;
      pick.ey = pick.ay + dy;
      return;
    }
    if (useCommandStore.getState().pickMode !== "none") {
      const rect = canvasRef.current!.getBoundingClientRect();
      pickHoverRef.current = [event.clientX - rect.left, event.clientY - rect.top];
      return;
    }
    pickHoverRef.current = null;
    const drag = dragRef.current;
    if (!drag || !viewRef.current) return;
    viewRef.current = {
      zoom: viewRef.current.zoom,
      panX: drag.panX + (event.clientX - drag.sx),
      panY: drag.panY + (event.clientY - drag.sy),
    };
  };

  const onPointerUp = () => {
    const pick = pickArrowRef.current;
    pickArrowRef.current = null;
    pickHoverRef.current = null;
    dragRef.current = null;
    setPanning(false);
    if (!pick || !viewRef.current) return;
    const [wx, wy] = screenToWorld(viewRef.current, pick.ax, pick.ay);
    // Screen y grows downward, world y upward — negate the y component.
    const dragged = Math.hypot(pick.ex - pick.ax, pick.ey - pick.ay) > 8;
    const yaw = dragged
      ? Math.round(Math.atan2(-(pick.ey - pick.ay), pick.ex - pick.ax) * 1000) / 1000
      : null; // plain click: navigate keeps the current heading
    useCommandStore.getState().send(
      pick.mode === "goal" ? "navigate_to_pose" : "set_initial_pose",
      { x: Math.round(wx * 100) / 100, y: Math.round(wy * 100) / 100, yaw },
    );
  };

  const zoomButtons = (factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas || !viewRef.current) return;
    viewRef.current = zoomAt(viewRef.current, canvas.clientWidth / 2, canvas.clientHeight / 2, factor);
  };

  const fit = () => {
    const canvas = canvasRef.current;
    if (!canvas || !map) return;
    setFollowRobot(false);
    viewRef.current = fitView(map, canvas.clientWidth, canvas.clientHeight);
  };

  if (!map) {
    return (
      <div className="map-empty">
        {mapQuery.isLoading ? "Loading map…" : "No map received from the robot yet."}
      </div>
    );
  }

  return (
    <div className="map-container">
      <canvas
        ref={canvasRef}
        className={`map-canvas ${panning ? "panning" : ""} ${pickMode !== "none" ? "picking" : ""}`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => { pickHoverRef.current = null; }}
      />
      <div className="map-controls">
        <button className="btn" onClick={() => zoomButtons(1.25)} title="Zoom in">
          <Plus size={15} />
        </button>
        <button className="btn" onClick={() => zoomButtons(1 / 1.25)} title="Zoom out">
          <Minus size={15} />
        </button>
        <button className="btn" onClick={fit} title="Fit map">
          <Maximize size={15} />
        </button>
        <button
          className={`btn ${followRobot ? "active" : ""}`}
          onClick={() => setFollowRobot(!followRobot)}
          title="Follow robot"
        >
          <Crosshair size={15} />
        </button>
      </div>
      <div className="map-meta">
        {map.name} · {map.resolution.toFixed(2)} m/cell · v{map.map_version}
      </div>
    </div>
  );
}

const LAYER_LABELS: Record<keyof MapLayers, string> = {
  lidar: "Laser points",
  plannedPath: "Planned path",
  trajectory: "Traveled path",
  goal: "Destination marker",
};

export function MapSettings() {
  const layers = useUiStore((state) => state.mapLayers);
  const toggleLayer = useUiStore((state) => state.toggleLayer);

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button title="Map layers">
          <Layers size={14} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="dropdown-content" sideOffset={4} align="end">
          <DropdownMenu.Label className="dropdown-label">Layers</DropdownMenu.Label>
          {(Object.keys(LAYER_LABELS) as (keyof MapLayers)[]).map((key) => (
            <DropdownMenu.Item
              key={key}
              className={`dropdown-item ${layers[key] ? "checked" : ""}`}
              onSelect={(event) => {
                event.preventDefault();
                toggleLayer(key);
              }}
            >
              {layers[key] ? <Check size={14} /> : <span style={{ width: 14 }} />}
              {LAYER_LABELS[key]}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Separator className="dropdown-separator" />
          <DropdownMenu.Label className="dropdown-label">Floor</DropdownMenu.Label>
          <DropdownMenu.Item className="dropdown-item" disabled
                             style={{ opacity: 0.5, cursor: "not-allowed" }}>
            <span style={{ width: 14 }} />
            1st floor — not ready
          </DropdownMenu.Item>
          <DropdownMenu.Item className="dropdown-item checked" onSelect={(e) => e.preventDefault()}>
            <Check size={14} />
            2nd floor
          </DropdownMenu.Item>
          <DropdownMenu.Item className="dropdown-item" disabled
                             style={{ opacity: 0.5, cursor: "not-allowed" }}>
            <span style={{ width: 14 }} />
            3rd floor — not ready
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
