import * as Dialog from "@radix-ui/react-dialog";
import { SoftwareStop } from "./SoftwareStop";
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
import { Suspense, useRef } from "react";

import { useLayoutStore } from "../stores/layoutStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useUiStore } from "../stores/uiStore";
import { WIDGET_REGISTRY } from "../widgets/registry";

export function WidgetFrame({ id }: { id: string }) {
  const definition = WIDGET_REGISTRY[id];
  const editMode = useLayoutStore((state) => state.editMode);
  const breakpoint = useLayoutStore(state => state.breakpoint);
  const editWidget = useLayoutStore(state => state.editWidget);
  const removeWidget = useLayoutStore((state) => state.removeWidget);
  const minimized = useLayoutStore((state) => id in state.minimized);
  const toggleMinimize = useLayoutStore((state) => state.toggleMinimize);
  const fullscreenWidget = useUiStore((state) => state.fullscreenWidget);
  const setFullscreen = useUiStore((state) => state.setFullscreen);
  const connectionState = useTelemetryStore((state) =>
    state.wsConnected ? state.connection.state : "offline",
  );

  const fullscreen = fullscreenWidget === id;
  const returnFocus = useRef<HTMLElement | null>(null);
  const wasFullscreen = useRef(false);
  if (fullscreen && !wasFullscreen.current) returnFocus.current = document.activeElement as HTMLElement;
  wasFullscreen.current = fullscreen;
  const Component = definition.component;
  const remove = () => {
    if (fullscreen) setFullscreen(null);
    removeWidget(id);
  };
  const showStale = connectionState === "offline" && definition.needsRobot;

  const frame = (
    <section
      className={`widget ${fullscreen ? "fullscreen" : ""} ${minimized && !fullscreen ? "minimized" : ""}`}
      aria-label={definition.title}
      data-tour={`widget-${id}`}
    >
      <header className="widget-header">
        <span className="drag-handle" aria-label="Drag to move" title="Drag to move">
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
              {editMode && ([ ["up", "Move up"], ["down", "Move down"], ["taller", "Taller"], ["shorter", "Shorter"], ["wider", "Wider"], ["narrower", "Narrower"] ] as const).filter(([action]) => !["wider", "narrower"].includes(action) || ["lg", "md"].includes(breakpoint)).map(([action, label]) => (
                <DropdownMenu.Item key={action} className="dropdown-item" onSelect={() => editWidget(id, action)}>{label}</DropdownMenu.Item>
              ))}
              {editMode && (
                <DropdownMenu.Item className="dropdown-item" onSelect={remove}>
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
          <button onClick={remove} title="Remove widget">
            <X size={14} />
          </button>
        )}
      </header>
      {fullscreen && id === "liveMap" && <SoftwareStop />}
      {(!minimized || fullscreen) && (
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
    return <Dialog.Root open onOpenChange={(open) => { if (!open) setFullscreen(null); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fullscreen-backdrop" />
        <Dialog.Content asChild aria-describedby={undefined} onCloseAutoFocus={event => {
          event.preventDefault();
          requestAnimationFrame(() => {
            const previous = returnFocus.current;
            if (previous?.isConnected) previous.focus();
            else document.querySelector<HTMLButtonElement>(`[data-tour="widget-${id}"] button[title="Full screen"]`)?.focus();
          });
        }}>
          <div className="fullscreen-dialog"><Dialog.Title className="sr-only">{definition.title}</Dialog.Title>{frame}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>;
  }
  return frame;
}
