import type { MapData } from "../../types/protocol";

/**
 * View transform between world metres (ROS map frame, +y up) and canvas
 * pixels (+y down). zoom is pixels per metre; pan is the screen position of
 * the world origin.
 */
export interface View {
  zoom: number;
  panX: number;
  panY: number;
}

export function worldToScreen(view: View, wx: number, wy: number): [number, number] {
  return [view.panX + wx * view.zoom, view.panY - wy * view.zoom];
}

export function screenToWorld(view: View, sx: number, sy: number): [number, number] {
  return [(sx - view.panX) / view.zoom, (view.panY - sy) / view.zoom];
}

export function fitView(map: MapData, canvasWidth: number, canvasHeight: number, padding = 20): View {
  const worldWidth = map.width * map.resolution;
  const worldHeight = map.height * map.resolution;
  const zoom = Math.min(
    (canvasWidth - padding * 2) / worldWidth,
    (canvasHeight - padding * 2) / worldHeight,
  );
  const centerX = map.origin.x + worldWidth / 2;
  const centerY = map.origin.y + worldHeight / 2;
  return {
    zoom,
    panX: canvasWidth / 2 - centerX * zoom,
    panY: canvasHeight / 2 + centerY * zoom,
  };
}

export function zoomAt(view: View, sx: number, sy: number, factor: number): View {
  const [wx, wy] = screenToWorld(view, sx, sy);
  const zoom = Math.min(400, Math.max(2, view.zoom * factor));
  return { zoom, panX: sx - wx * zoom, panY: sy + wy * zoom };
}

export function followView(view: View, canvasWidth: number, canvasHeight: number, wx: number, wy: number): View {
  return {
    zoom: view.zoom,
    panX: canvasWidth / 2 - wx * view.zoom,
    panY: canvasHeight / 2 + wy * view.zoom,
  };
}
