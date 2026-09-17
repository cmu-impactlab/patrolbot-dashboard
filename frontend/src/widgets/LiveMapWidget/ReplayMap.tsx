import { Crosshair, Maximize, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMapQuery } from "../../api/queries";
import { useReplayStore } from "../../stores/replayStore";
import { ROBOT_LENGTH_M, traceFootprint } from "../../lib/robotGeometry";
import type { MapData } from "../../types/protocol";
import { MapGesture } from "./gestures";
import { buildMapBitmap } from "./bitmap";
import { cssVar } from "./colors";
import { fitView, followView, fitPoints, zoomAt, worldToScreen, type View } from "./transform";

/**
 * The map as it was during a recording — and nothing else.
 *
 * Deliberately separate from LiveMapWidget: this canvas never reads the
 * telemetry store, so a replay can never be mistaken for the robot's current
 * position, and the live map never has a second robot drawn on it. The two
 * share the view transform, the map bitmap and the footprint geometry, which
 * is what actually needs to stay consistent between them.
 */

/** Below this on-screen size the footprint is unreadable, so a dot stands in. */
const MIN_LEGIBLE_ROBOT_PX = 14;

function drawReplayScene(
  ctx: CanvasRenderingContext2D,
  view: View,
  map: MapData,
  bitmap: HTMLCanvasElement,
): void {
  const width = ctx.canvas.clientWidth;
  const height = ctx.canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);

  const [mapLeft, mapTop] =
    worldToScreen(view, map.origin.x, map.origin.y + map.height * map.resolution);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(bitmap, mapLeft, mapTop,
                map.width * map.resolution * view.zoom,
                map.height * map.resolution * view.zoom);

  const replay = useReplayStore.getState();
  const { poses } = replay;
  if (poses.length === 0) return;
  const t = replay.now();

  // The whole route, faint: where the robot went over the entire recording.
  if (poses.length > 1) {
    ctx.beginPath();
    for (let i = 0; i < poses.length; i++) {
      const [sx, sy] = worldToScreen(view, poses[i].x, poses[i].y);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.strokeStyle = cssVar("--muted");
    ctx.lineWidth = 1.3;
    ctx.setLineDash([3, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // The part already played, solid — the trail behind the robot.
  ctx.beginPath();
  let drawn = 0;
  for (let i = 0; i < poses.length && poses[i].tMs <= t; i++) {
    const [sx, sy] = worldToScreen(view, poses[i].x, poses[i].y);
    if (drawn === 0) ctx.moveTo(sx, sy);
    else ctx.lineTo(sx, sy);
    drawn++;
  }
  const here = replay.poseAt(t);
  if (here && drawn > 0) {
    const [sx, sy] = worldToScreen(view, here.x, here.y);
    ctx.lineTo(sx, sy);
  }
  if (drawn > 0) {
    ctx.strokeStyle = cssVar("--info");
    ctx.lineWidth = 2.6;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }

  // Start marker, so a loop is readable even when the route doubles back.
  const [startX, startY] = worldToScreen(view, poses[0].x, poses[0].y);
  ctx.beginPath();
  ctx.arc(startX, startY, 4.5, 0, Math.PI * 2);
  ctx.fillStyle = cssVar("--surface");
  ctx.fill();
  ctx.strokeStyle = cssVar("--muted");
  ctx.lineWidth = 2;
  ctx.stroke();

  if (!here) return;

  // The robot at the playhead, at its true footprint size and filled — on this
  // canvas there is no live robot to confuse it with.
  const [rx, ry] = worldToScreen(view, here.x, here.y);
  const angle = -here.yaw; // screen y is flipped
  ctx.save();
  ctx.translate(rx, ry);
  ctx.rotate(angle);
  traceFootprint(ctx, view.zoom);
  ctx.fillStyle = cssVar("--info");
  ctx.strokeStyle = cssVar("--surface");
  ctx.lineWidth = Math.max(0.75, Math.min(1.8, (ROBOT_LENGTH_M * view.zoom) / 24));
  ctx.fill();
  ctx.stroke();
  // Heading wedge, so the direction of travel is readable at a glance.
  if (ROBOT_LENGTH_M * view.zoom >= 28) {
    ctx.beginPath();
    ctx.moveTo(0.21 * view.zoom, 0);
    ctx.lineTo(-0.02 * view.zoom, 0.1 * view.zoom);
    ctx.lineTo(-0.02 * view.zoom, -0.1 * view.zoom);
    ctx.closePath();
    ctx.fillStyle = cssVar("--surface");
    ctx.fill();
  }
  ctx.restore();

  if (ROBOT_LENGTH_M * view.zoom < MIN_LEGIBLE_ROBOT_PX) {
    ctx.beginPath();
    ctx.arc(rx, ry, 5, 0, Math.PI * 2);
    ctx.fillStyle = cssVar("--info");
    ctx.fill();
    ctx.strokeStyle = cssVar("--surface");
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

export function ReplayMap({ theme }: { theme: string }) {
  const mapQuery = useMapQuery(0);
  const poses = useReplayStore((state) => state.poses);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View | null>(null);
  const bitmapRef = useRef<{ key: string; bitmap: HTMLCanvasElement } | null>(null);
  const signatureRef = useRef("");
  const gesture = useRef(new MapGesture());
  const [interacting, setInteracting] = useState(false);
  const [panning, setPanning] = useState(false);
  const [follow, setFollow] = useState(false);

  const map = mapQuery.data ?? null;

  // The view starts framed on the route rather than on the whole building: a
  // patrol usually covers a small part of the map, and fitting the map first
  // would leave the robot as a speck.
  const fitToRoute = () => {
    const canvas = canvasRef.current;
    if (!canvas || !map) return;
    setFollow(false);
    viewRef.current = poses.length > 0
      ? fitPoints(poses.map((pose) => [pose.x, pose.y]), canvas.clientWidth, canvas.clientHeight)
      : fitView(map, canvas.clientWidth, canvas.clientHeight);
  };

  // Re-frame once the route arrives (the recording loads after first paint).
  useEffect(() => {
    viewRef.current = null;
    signatureRef.current = "";
    gesture.current.cancel();
    setPanning(false);
  }, [poses]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !map) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // no 2D context (headless render): the rest of the page still works
    let raf = 0;

    const render = () => {
      const dpr = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const pixelWidth = Math.round(width * dpr);
      const pixelHeight = Math.round(height * dpr);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const bitmapKey = `${map.map_version}:${theme}`;
      if (!bitmapRef.current || bitmapRef.current.key !== bitmapKey) {
        bitmapRef.current = { key: bitmapKey, bitmap: buildMapBitmap(map) };
      }
      if (!viewRef.current) {
        const replayPoses = useReplayStore.getState().poses;
        viewRef.current = replayPoses.length > 0
          ? fitPoints(replayPoses.map((pose) => [pose.x, pose.y]), width, height)
          : fitView(map, width, height);
      }

      const replay = useReplayStore.getState();
      const t = replay.now();
      if (follow) {
        const here = replay.poseAt(t);
        if (here) viewRef.current = followView(viewRef.current, width, height, here.x, here.y);
      }

      // Repaint only when something moved — rescaling the full-resolution map
      // bitmap every frame is what made the live map sluggish.
      const view = viewRef.current;
      const signature = [
        theme, canvas.width, canvas.height, view.zoom, view.panX, view.panY,
        replay.poses.length, Math.round(t / 33),
      ].join("|");
      if (signature !== signatureRef.current) {
        signatureRef.current = signature;
        drawReplayScene(ctx, view, map, bitmapRef.current.bitmap);
      }
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);
    return () => cancelAnimationFrame(raf);
  }, [map, theme, follow]);

  const onWheel = (event: React.WheelEvent) => {
    if (!viewRef.current) return;
    const rect = canvasRef.current!.getBoundingClientRect();
    viewRef.current = zoomAt(viewRef.current, event.clientX - rect.left,
                             event.clientY - rect.top, event.deltaY < 0 ? 1.15 : 1 / 1.15);
  };

  const point = (event: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const onPointerDown = (event: React.PointerEvent) => {
    if (!viewRef.current || (event.pointerType !== "mouse" && !interacting)) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current.down(event.pointerId, point(event));
    setPanning(true); setFollow(false);
  };
  const onPointerMove = (event: React.PointerEvent) => {
    if (viewRef.current) viewRef.current = gesture.current.move(event.pointerId, point(event), viewRef.current);
  };
  const onPointerUp = (event: React.PointerEvent) => {
    gesture.current.up(event.pointerId); setPanning(gesture.current.pointers.size > 0);
  };
  const cancel = () => { gesture.current.cancel(); setPanning(false); };
  useEffect(() => {
    document.addEventListener("visibilitychange", cancel);
    return () => document.removeEventListener("visibilitychange", cancel);
  }, []);

  const zoomBy = (factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas || !viewRef.current) return;
    viewRef.current = zoomAt(viewRef.current, canvas.clientWidth / 2, canvas.clientHeight / 2, factor);
  };

  if (!map) {
    return (
      <div className="map-empty">
        {mapQuery.isLoading ? "Loading map…" : "No map available to replay against."}
      </div>
    );
  }

  return (
    <div className="map-container">
      <canvas
        ref={canvasRef}
        className={`map-canvas ${panning ? "panning" : ""}`}
        aria-label="Recorded robot map"
        style={{ touchAction: interacting ? "none" : "pan-y pinch-zoom" }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={cancel}
        onLostPointerCapture={onPointerUp}
      />
<div className="map-interaction"><button className="btn" onClick={() => { cancel(); setInteracting(!interacting); }}>{interacting ? "Done" : "Interact with map"}</button></div>
      <div className="map-controls">
        <button className="btn" onClick={() => zoomBy(1.25)} title="Zoom in">
          <Plus size={15} />
        </button>
        <button className="btn" onClick={() => zoomBy(1 / 1.25)} title="Zoom out">
          <Minus size={15} />
        </button>
        <button className="btn" onClick={fitToRoute} title="Fit the recorded route">
          <Maximize size={15} />
        </button>
        <button className={`btn ${follow ? "active" : ""}`} onClick={() => setFollow(!follow)}
                title="Follow the robot through the replay">
          <Crosshair size={15} />
        </button>
      </div>
      <div className="map-meta">{map.name} · {map.resolution.toFixed(2)} m/cell</div>
    </div>
  );
}
