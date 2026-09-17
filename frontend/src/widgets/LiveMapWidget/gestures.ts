import { zoomAt, type View } from "./transform";

export interface Point { x: number; y: number }
/** Pointer identity, cancellation, and pinch math shared by both maps. */
export class MapGesture {
  pointers = new Map<number, Point>();
  multi = false;
  down(id: number, point: Point) {
    if (!this.pointers.size) this.multi = false;
    this.pointers.set(id, point);
    if (this.pointers.size > 1) this.multi = true;
  }
  move(id: number, point: Point, view: View): View {
    const before = [...this.pointers.values()];
    const old = this.pointers.get(id);
    if (!old) return view;
    this.pointers.set(id, point);
    if (before.length < 2) return { ...view, panX: view.panX + point.x - old.x, panY: view.panY + point.y - old.y };
    const after = [...this.pointers.values()];
    const center = (points: Point[]) => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 });
    const distance = (points: Point[]) => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
    const a = center(before), b = center(after);
    const zoomed = zoomAt(view, a.x, a.y, distance(after) / Math.max(1, distance(before)));
    return { ...zoomed, panX: zoomed.panX + b.x - a.x, panY: zoomed.panY + b.y - a.y };
  }
  up(id: number): boolean {
    const selectable = this.pointers.has(id) && !this.multi && this.pointers.size === 1;
    this.pointers.delete(id);
    return selectable;
  }
  cancel() { this.pointers.clear(); this.multi = false; }
}
