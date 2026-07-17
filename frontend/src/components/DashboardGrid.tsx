import { useEffect, useRef, useState } from "react";
import { Responsive, type Layouts } from "react-grid-layout";
import { useSaveLayout } from "../api/queries";
import { useLayoutStore } from "../stores/layoutStore";
import { WIDGET_REGISTRY } from "../widgets/registry";
import { WidgetFrame } from "./WidgetFrame";

/**
 * Container width via ResizeObserver rather than react-grid-layout's
 * WidthProvider: WidthProvider only re-measures on window resize events and
 * can keep a stale width (e.g. resize while loading, scrollbar appearing),
 * which mispositions every widget and then gets persisted by the autosave.
 */
function useContainerWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0].contentRect.width);
      if (next > 0) setWidth(next);
    });
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export function DashboardGrid() {
  const widgets = useLayoutStore((state) => state.widgets);
  const layouts = useLayoutStore((state) => state.layouts);
  const editMode = useLayoutStore((state) => state.editMode);
  const dirty = useLayoutStore((state) => state.dirty);
  const setLayouts = useLayoutStore((state) => state.setLayouts);
  const markSaved = useLayoutStore((state) => state.markSaved);
  const save = useSaveLayout();
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [containerRef, width] = useContainerWidth();

  // Debounced persistence: 1 s after the last layout change.
  useEffect(() => {
    if (!dirty) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      save.mutate(useLayoutStore.getState().doc());
      markSaved();
    }, 1000);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [dirty, layouts, widgets, save, markSaved]);

  const known = widgets.filter((id) => id in WIDGET_REGISTRY);

  return (
    <div ref={containerRef} className={editMode ? "edit-mode" : ""}>
      {width > 0 && (
        <Responsive
          className="layout"
          width={width}
          layouts={layouts as Layouts}
          breakpoints={{ lg: 996, md: 768, sm: 480, xs: 0 }}
          cols={{ lg: 12, md: 10, sm: 6, xs: 4 }}
          rowHeight={32}
          margin={[10, 10]}
          draggableHandle=".drag-handle"
          isDraggable={editMode}
          isResizable={editMode}
          onLayoutChange={(_current, all) => setLayouts(all, editMode)}
        >
          {known.map((id) => (
            <div key={id}>
              <WidgetFrame id={id} />
            </div>
          ))}
        </Responsive>
      )}
    </div>
  );
}
