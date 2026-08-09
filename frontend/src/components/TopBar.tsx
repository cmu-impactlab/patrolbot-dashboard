import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, LayoutDashboard, LogOut, Moon, Pencil, Plus, Save, Sun, Trash2 } from "lucide-react";
import { useState } from "react";
import cmuqLogo from "../assets/cmuq-logo.png";
import { useDeleteLayout, useSaveNamedLayout } from "../api/queries";
import { useAuthStore } from "../stores/authStore";
import { useLayoutStore } from "../stores/layoutStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useUiStore } from "../stores/uiStore";
import { CONNECTION_COPY, STATUS_COPY } from "../lib/plainLanguage";
import { WidgetLibrary } from "./WidgetLibrary";
import { DashboardTourButton } from "./DashboardTourButton";

export function TopBar() {
  const status = useTelemetryStore((state) => state.status);
  const connection = useTelemetryStore((state) => state.connection);
  const wsConnected = useTelemetryStore((state) => state.wsConnected);
  const robotId = useTelemetryStore((state) => state.robotId);
  const editMode = useLayoutStore((state) => state.editMode);
  const setEditMode = useLayoutStore((state) => state.setEditMode);
  const presets = useLayoutStore((state) => state.presets);
  const customLayouts = useLayoutStore((state) => state.customLayouts);
  const activePreset = useLayoutStore((state) => state.activePreset);
  const applyPreset = useLayoutStore((state) => state.applyPreset);
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const authUser = useAuthStore((state) => state.user);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveError, setSaveError] = useState("");
  const saveNamed = useSaveNamedLayout();
  const deleteLayout = useDeleteLayout();

  const saveCurrentAs = () => {
    const name = saveName.trim();
    if (!name) return;
    if (name.toLowerCase() === "current" || presets.some((preset) => preset.name.toLowerCase() === name.toLowerCase())) {
      setSaveError("That name is reserved — pick another.");
      return;
    }
    const state = useLayoutStore.getState();
    const doc = { widgets: state.widgets, layouts: state.layouts, preset: name };
    saveNamed.mutate({ name, doc }, {
      onSuccess: () => {
        useLayoutStore.getState().registerCustom({
          name, is_preset: false, layout: doc, updated_at: new Date().toISOString(),
        });
        setSaveOpen(false);
        setSaveName("");
        setSaveError("");
      },
      onError: () => setSaveError("Saving failed — is the dashboard server running?"),
    });
  };

  const removeCustom = (name: string) => {
    deleteLayout.mutate(name, {
      onSuccess: () => useLayoutStore.getState().dropCustom(name),
    });
  };

  const statusCopy = STATUS_COPY[status.status];
  const connState = wsConnected ? connection.state : "offline";
  const connCopy = CONNECTION_COPY[connState];

  return (
    <header className="topbar" data-tour="overview">
      <div className="brand" data-tour="robot-id">
        <img className="brand-mark" src={cmuqLogo} alt="Carnegie Mellon University Qatar" />
        <span>{robotId}</span>
      </div>
      <span className={`status-pill tone-${statusCopy.tone}`} title={status.detail} data-tour="robot-condition">
        <span className="dot" />
        {statusCopy.label}
      </span>
      <span className="conn-badge" title={connCopy.description} data-tour="connection">
        <span className={`dot ${connState}`} />
        {wsConnected ? connCopy.label : "Dashboard reconnecting…"}
      </span>
      <div className="spacer" />

      <DropdownMenu.Root>
        <DropdownMenu.Trigger asChild>
          <button className="btn" data-tour="layouts">
            <LayoutDashboard size={15} />
            {activePreset ?? "Custom layout"}
            <ChevronDown size={14} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className="dropdown-content" sideOffset={6} align="end">
            <DropdownMenu.Label className="dropdown-label">Presets</DropdownMenu.Label>
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
            {customLayouts.length > 0 && (
              <>
                <DropdownMenu.Separator className="dropdown-separator" />
                <DropdownMenu.Label className="dropdown-label">My dashboards</DropdownMenu.Label>
                {customLayouts.map((layout) => (
                  <DropdownMenu.Item
                    key={layout.name}
                    className={`dropdown-item ${layout.name === activePreset ? "checked" : ""}`}
                    onSelect={() => applyPreset(layout.name)}
                  >
                    {layout.name === activePreset ? <Check size={14} /> : <span style={{ width: 14 }} />}
                    <span style={{ flex: 1 }}>{layout.name}</span>
                    <button
                      className="dropdown-delete"
                      title={`Delete "${layout.name}"`}
                      onClick={(event) => {
                        event.stopPropagation();
                        event.preventDefault();
                        removeCustom(layout.name);
                      }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </DropdownMenu.Item>
                ))}
              </>
            )}
            <DropdownMenu.Separator className="dropdown-separator" />
            <DropdownMenu.Item className="dropdown-item" onSelect={() => setSaveOpen(true)}>
              <Save size={14} />
              Save current as…
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      <Dialog.Root open={saveOpen} onOpenChange={(open) => { setSaveOpen(open); setSaveError(""); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" style={{ maxWidth: 380 }}>
            <Dialog.Title asChild>
              <h2>Save dashboard</h2>
            </Dialog.Title>
            <p className="subtext" style={{ marginTop: 0 }}>
              Saves the current widgets and layout as a dashboard you can switch back to any time.
            </p>
            <input
              className="text-input"
              placeholder="Dashboard name"
              value={saveName}
              autoFocus
              onChange={(event) => { setSaveName(event.target.value); setSaveError(""); }}
              onKeyDown={(event) => { if (event.key === "Enter") saveCurrentAs(); }}
            />
            {saveError && <p className="subtext" style={{ color: "var(--danger)" }}>{saveError}</p>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
              <button className="btn" onClick={() => setSaveOpen(false)}>Cancel</button>
              <button className="btn primary" disabled={!saveName.trim() || saveNamed.isPending} onClick={saveCurrentAs}>
                Save
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {editMode && (
        <button className="btn" onClick={() => setLibraryOpen(true)}>
          <Plus size={15} /> Add widget
        </button>
      )}
      <button
        className={`btn ${editMode ? "active" : ""}`}
        data-tour="edit-dashboard"
        onClick={() => setEditMode(!editMode)}
        title="Toggle Edit Dashboard mode — widgets can only be moved while editing"
      >
        <Pencil size={14} />
        {editMode ? "Done editing" : "Edit dashboard"}
      </button>
      <DashboardTourButton />
      <button
        className="btn icon"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        title="Toggle light/dark theme"
      >
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </button>
      {authUser?.auth_mode === "oidc" && (
        <span className="user-chip" data-tour="account" title={`Signed in as ${authUser.display_name} (${authUser.role})`}>
          {authUser.username}
          <a className="btn icon" href="/auth/logout" title="Sign out">
            <LogOut size={14} />
          </a>
        </span>
      )}
      <WidgetLibrary open={libraryOpen} onOpenChange={setLibraryOpen} />
    </header>
  );
}
