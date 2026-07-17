import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, Crosshair, Layers, Maximize, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMapQuery } from "../../api/queries";
import { useTelemetryStore } from "../../stores/telemetryStore";
import { useUiStore, type MapLayers } from "../../stores/uiStore";
import type { MapData } from "../../types/protocol";
import { buildMapBitmap } from "./bitmap";
import { fitView, followView, worldToScreen, zoomAt, type View } from "./transform";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawScene(
  ctx: CanvasRenderingContext2D,
  view: View,
  map: MapData,
  bitmap: HTMLCanvasElement,
  layers: MapLayers,
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
      drawScene(ctx, viewRef.current, map, bitmapRef.current.bitmap, useUiStore.getState().mapLayers);
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
    const drag = dragRef.current;
    if (!drag || !viewRef.current) return;
    viewRef.current = {
      zoom: viewRef.current.zoom,
      panX: drag.panX + (event.clientX - drag.sx),
      panY: drag.panY + (event.clientY - drag.sy),
    };
  };

  const onPointerUp = () => {
    dragRef.current = null;
    setPanning(false);
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
        className={`map-canvas ${panning ? "panning" : ""}`}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
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
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
