import { useEffect, useRef } from "react";
import { useCommandStore } from "../stores/commandStore";
import { useLayoutStore } from "../stores/layoutStore";
import { useUiStore } from "../stores/uiStore";
import { WidgetFrame } from "./WidgetFrame";

/** Selection is reachable even when the chosen dashboard omits the map. */
export function MapSelectionHost() {
  const pickMode = useCommandStore(state => state.pickMode);
  const widgets = useLayoutStore(state => state.widgets);
  const fullscreen = useUiStore(state => state.fullscreenWidget);
  const wasFullscreen = useRef(false);
  useEffect(() => {
    if (pickMode === "none") return;
    const layout = useLayoutStore.getState();
    if ("liveMap" in layout.minimized) layout.toggleMinimize("liveMap");
    if (!layout.widgets.includes("liveMap") || window.innerWidth < 768) {
      useUiStore.getState().setFullscreen("liveMap");
    } else {
      document.querySelector('[data-tour="widget-liveMap"]')?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [pickMode]);
  useEffect(() => {
    if (wasFullscreen.current && fullscreen !== "liveMap") useCommandStore.getState().setPickMode("none");
    wasFullscreen.current = fullscreen === "liveMap";
  }, [fullscreen]);
  return !widgets.includes("liveMap") && fullscreen === "liveMap" ? <WidgetFrame id="liveMap" /> : null;
}
