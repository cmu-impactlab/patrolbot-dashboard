import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  ChevronDown,
  ChevronUp,
  GripVertical,
  Maximize2,
  Minimize2,
  MoreVertical,
  X,
} from "lucide-react";
import { Suspense, useEffect } from "react";
import { createPortal } from "react-dom";
import { useLayoutStore } from "../stores/layoutStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useUiStore } from "../stores/uiStore";
import { WIDGET_REGISTRY } from "../widgets/registry";

export function WidgetFrame({ id }: { id: string }) {
  const definition = WIDGET_REGISTRY[id];
  const editMode = useLayoutStore((state) => state.editMode);
  const removeWidget = useLayoutStore((state) => state.removeWidget);
  const minimized = useLayoutStore((state) => id in state.minimized);
  const toggleMinimize = useLayoutStore((state) => state.toggleMinimize);
  const fullscreenWidget = useUiStore((state) => state.fullscreenWidget);
  const setFullscreen = useUiStore((state) => state.setFullscreen);
  const connectionState = useTelemetryStore((state) =>
    state.wsConnected ? state.connection.state : "offline",
  );

  const fullscreen = fullscreenWidget === id;
  const Component = definition.component;
  const showStale = connectionState === "offline" && definition.needsRobot;

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFullscreen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen, setFullscreen]);

  const frame = (
    <section
      className={`widget ${fullscreen ? "fullscreen" : ""} ${minimized ? "minimized" : ""}`}
      aria-label={definition.title}
      data-tour={`widget-${id}`}
    >
      <header className="widget-header">
        <span className="drag-handle" title="Drag to move">
          <GripVertical size={14} />
        </span>
        <span className="title">{definition.title}</span>
        {definition.settings && <definition.settings />}
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button title="Widget menu">
              <MoreVertical size={14} />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content className="dropdown-content" sideOffset={4} align="end">
              <DropdownMenu.Item
                className="dropdown-item"
                onSelect={() => setFullscreen(fullscreen ? null : id)}
              >
                {fullscreen ? "Exit full screen" : "Full screen"}
              </DropdownMenu.Item>
              <DropdownMenu.Item className="dropdown-item" onSelect={() => toggleMinimize(id)}>
                {minimized ? "Expand" : "Minimize"}
              </DropdownMenu.Item>
              {editMode && (
                <DropdownMenu.Item className="dropdown-item" onSelect={() => removeWidget(id)}>
                  Remove from dashboard
                </DropdownMenu.Item>
              )}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
        <button onClick={() => toggleMinimize(id)} title={minimized ? "Expand" : "Minimize"}>
          {minimized ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        <button
          onClick={() => setFullscreen(fullscreen ? null : id)}
          title={fullscreen ? "Exit full screen" : "Full screen"}
        >
          {fullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
        </button>
        {editMode && (
          <button onClick={() => removeWidget(id)} title="Remove widget">
            <X size={14} />
          </button>
        )}
      </header>
      {!minimized && (
        <div className={`widget-body ${definition.noPadding ? "no-pad" : ""}`}>
          {showStale && (
            <div className="stale-overlay">
              The robot is offline — showing the last known data.
            </div>
          )}
          <Suspense fallback={null}>
            <Component />
          </Suspense>
        </div>
      )}
    </section>
  );

  if (fullscreen) {
    // Portal to <body>: grid items carry a CSS transform, which would make
    // position:fixed resolve against the item instead of the viewport.
    return createPortal(
      <>
        <div className="fullscreen-backdrop" onClick={() => setFullscreen(null)} />
        {frame}
      </>,
      document.body,
    );
  }
  return frame;
}
