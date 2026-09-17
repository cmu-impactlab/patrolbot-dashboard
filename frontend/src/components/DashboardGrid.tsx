import { useEffect, useRef, useState } from "react";
import { Responsive, type Layouts } from "react-grid-layout";
import { useSaveLayout } from "../api/queries";
import { BREAKPOINTS, COLUMNS, breakpointFor, useLayoutStore } from "../stores/layoutStore";
import { WIDGET_REGISTRY } from "../widgets/registry";
import { WidgetFrame } from "./WidgetFrame";

// react-grid-layout uses > while our integer container boundaries are inclusive.
const gridBreakpoints = Object.fromEntries(Object.entries(BREAKPOINTS).map(([key, value]) => [key, Math.max(0, value - 1)]));

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
  const minimized = useLayoutStore(state => state.minimized);
  const revision = useLayoutStore(state => state.revision);
  const { mutate, isPending, isError, reset } = save;
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [containerRef, width] = useContainerWidth();

  // Debounced persistence: 1 s after the last layout change.
  useEffect(() => {
    if (!dirty || isPending || isError) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      const state = useLayoutStore.getState();
      const savingRevision = state.revision;
      mutate(state.doc(), { onSuccess: () => markSaved(savingRevision) });
    }, 1000);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [dirty, revision, isPending, isError, mutate, markSaved]);

  useEffect(() => { useLayoutStore.getState().setBreakpoint(breakpointFor(width)); }, [width]);
  const displayLayouts = Object.fromEntries(Object.entries(layouts).map(([key, items]) =>
    [key, items.map(item => item.i in minimized ? { ...item, h: 2, minH: 2 } : { ...item })]));
  const known = widgets.filter((id) => id in WIDGET_REGISTRY);

  return (
    <div ref={containerRef} className={editMode ? "edit-mode" : ""}>
      {isError && <div role="alert">Dashboard changes are unsaved. <button className="btn" onClick={() => reset()}>Retry saving</button></div>}
      {width > 0 && (
        <Responsive
          className="layout"
          width={width}
          layouts={displayLayouts as Layouts}
          breakpoints={gridBreakpoints}
          cols={COLUMNS}
          rowHeight={32}
          margin={[10, 10]}
          draggableHandle=".drag-handle"
          isDraggable={editMode}
          isResizable={editMode}
          resizeHandles={["se", "sw"]}
          onDragStop={(current) => setLayouts({ [breakpointFor(width)]: current })}
          onResizeStop={(current) => setLayouts({ [breakpointFor(width)]: current })}
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
