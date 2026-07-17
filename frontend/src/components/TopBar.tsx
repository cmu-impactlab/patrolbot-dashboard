import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, LayoutDashboard, Moon, Pencil, Plus, Sun } from "lucide-react";
import { useState } from "react";
import { useLayoutStore } from "../stores/layoutStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useUiStore } from "../stores/uiStore";
import { CONNECTION_COPY, STATUS_COPY } from "../lib/plainLanguage";
import { WidgetLibrary } from "./WidgetLibrary";

export function TopBar() {
  const status = useTelemetryStore((state) => state.status);
  const connection = useTelemetryStore((state) => state.connection);
  const wsConnected = useTelemetryStore((state) => state.wsConnected);
  const robotId = useTelemetryStore((state) => state.robotId);
  const editMode = useLayoutStore((state) => state.editMode);
  const setEditMode = useLayoutStore((state) => state.setEditMode);
  const presets = useLayoutStore((state) => state.presets);
  const activePreset = useLayoutStore((state) => state.activePreset);
  const applyPreset = useLayoutStore((state) => state.applyPreset);
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const [libraryOpen, setLibraryOpen] = useState(false);

  const statusCopy = STATUS_COPY[status.status];
  const connState = wsConnected ? connection.state : "offline";
  const connCopy = CONNECTION_COPY[connState];

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">PB</div>
        <span>{robotId}</span>
      </div>
      <span className={`status-pill tone-${statusCopy.tone}`} title={status.detail}>
        <span className="dot" />
        {statusCopy.label}
      </span>
      <span className="conn-badge" title={connCopy.description}>
        <span className={`dot ${connState}`} />
        {wsConnected ? connCopy.label : "Dashboard reconnecting…"}
      </span>
      <div className="spacer" />

      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button className="btn">
            <LayoutDashboard size={15} />
            {activePreset ?? "Custom layout"}
            <ChevronDown size={14} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className="dropdown-content" sideOffset={6} align="end">
            {presets.map((preset) => (
              <DropdownMenu.Item
                key={preset.name}
                className={`dropdown-item ${preset.name === activePreset ? "checked" : ""}`}
                onSelect={() => applyPreset(preset.name)}
              >
                {preset.name === activePreset ? <Check size={14} /> : <span style={{ width: 14 }} />}
                {preset.name}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {editMode && (
        <button className="btn" onClick={() => setLibraryOpen(true)}>
          <Plus size={15} /> Add widget
        </button>
      )}
      <button
        className={`btn ${editMode ? "active" : ""}`}
        onClick={() => setEditMode(!editMode)}
        title="Toggle Edit Dashboard mode — widgets can only be moved while editing"
      >
        <Pencil size={14} />
        {editMode ? "Done editing" : "Edit dashboard"}
      </button>
      <button
        className="btn icon"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        title="Toggle light/dark theme"
      >
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </button>
      <WidgetLibrary open={libraryOpen} onOpenChange={setLibraryOpen} />
    </header>
  );
}
