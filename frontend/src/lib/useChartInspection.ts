import { useState, type PointerEvent } from "react";

/** Touch taps retain a reading; a mouse still gets ordinary hover tooltips. */
export function useChartInspection() {
  const [trigger, setTrigger] = useState<"hover" | "click">("hover");
  const onPointerDownCapture = (event: PointerEvent) => setTrigger(event.pointerType === "mouse" ? "hover" : "click");
  const onPointerMoveCapture = (event: PointerEvent) => { if (event.pointerType === "mouse") setTrigger("hover"); };
  return { trigger, events: { onPointerDownCapture, onPointerMoveCapture } };
}
