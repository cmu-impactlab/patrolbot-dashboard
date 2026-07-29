import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, Circle, Download, Play, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import {
  useDeleteRecording,
  useRecordings,
  useStartRecording,
  useStopRecording,
} from "../api/queries";
import type { RecordingRow } from "../stores/replayStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { formatTime } from "../lib/format";
import { openReplayTab } from "../lib/replayTab";

/** Plain-language channel labels; ids match the server's recorder channels. */
const CHANNELS: { id: string; label: string; default: boolean }[] = [
  { id: "pose", label: "Position & speed", default: true },
  { id: "lidar", label: "Laser scan", default: false },
  { id: "path", label: "Planned path", default: false },
  { id: "battery", label: "Battery", default: true },
  { id: "base_state", label: "Drive base state", default: false },
  { id: "diagnostics", label: "System reports", default: false },
  { id: "event", label: "Alerts", default: true },
];

const CHANNEL_LABELS = new Map(CHANNELS.map((channel) => [channel.id, channel.label]));

/** SQLite datetime('now') → parseable ISO ("YYYY-MM-DD HH:MM:SS" is UTC). */
function iso(sqlite: string): string {
  return sqlite.includes("T") ? sqlite : sqlite.replace(" ", "T") + "Z";
}

function durationLabel(row: RecordingRow): string {
  if (!row.ended_at) return "…";
  const seconds = Math.max(0, (Date.parse(iso(row.ended_at)) - Date.parse(iso(row.started_at))) / 1000);
  if (seconds < 90) return `${Math.round(seconds)} s`;
  return `${Math.round(seconds / 60)} min`;
}

/**
 * Download picker: a zip holding one CSV per channel.
 *
 * The channels are opt-out rather than opt-in — the common case is "give me
 * everything I recorded" — but a lidar scan dwarfs every other channel put
 * together, so being able to leave it out is what makes the rest usable.
 */
function DownloadMenu({ row }: { row: RecordingRow }) {
  const available = (row.channels ?? []).filter((id) => CHANNEL_LABELS.has(id));
  const [excluded, setExcluded] = useState<string[]>([]);
  const selected = available.filter((id) => !excluded.includes(id));

  const href = selected.length === available.length
    ? `/api/recordings/${row.id}/export.zip`
    : `/api/recordings/${row.id}/export.zip?channels=${selected.join(",")}`;

  // A recording from before channels were tracked has nothing to pick from;
  // the plain link still exports everything the server finds.
  if (available.length === 0) {
    return (
      <a className="btn icon" title="Download data (.zip of CSVs)"
         href={`/api/recordings/${row.id}/export.zip`} download>
        <Download size={14} />
      </a>
    );
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="btn icon" title="Download data (.zip of CSVs)">
          <Download size={14} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="dropdown-content" sideOffset={4} align="end">
          <DropdownMenu.Label className="dropdown-label">
            One CSV per channel
          </DropdownMenu.Label>
          {available.map((id) => (
            <DropdownMenu.Item
              key={id}
              className={`dropdown-item ${excluded.includes(id) ? "" : "checked"}`}
              onSelect={(event) => {
                event.preventDefault();
                setExcluded((current) =>
                  current.includes(id) ? current.filter((c) => c !== id) : [...current, id]);
              }}
            >
              {excluded.includes(id) ? <span style={{ width: 14 }} /> : <Check size={14} />}
              {CHANNEL_LABELS.get(id)}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Separator className="dropdown-separator" />
          <DropdownMenu.Item asChild disabled={selected.length === 0}>
            <a className="dropdown-item" href={href} download
               aria-disabled={selected.length === 0}
               onClick={(event) => { if (selected.length === 0) event.preventDefault(); }}>
              <Download size={14} />
              {selected.length === 0
                ? "Pick at least one channel"
                : `Download ${selected.length} CSV${selected.length === 1 ? "" : "s"} (.zip)`}
            </a>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function RecordingsWidget() {
  const listQuery = useRecordings();
  const start = useStartRecording();
  const stopMutation = useStopRecording();
  const remove = useDeleteRecording();
  const online = useTelemetryStore((state) => state.connection.state === "online");
  const [name, setName] = useState("");
  const [channels, setChannels] = useState<string[]>(
    CHANNELS.filter((channel) => channel.default).map((channel) => channel.id));
  const [error, setError] = useState("");

  const rows = listQuery.data ?? [];
  const activeRow = rows.find((row) => row.status === "recording") ?? null;

  const toggleChannel = (id: string) =>
    setChannels((current) =>
      current.includes(id) ? current.filter((c) => c !== id) : [...current, id]);

  const begin = () => {
    setError("");
    start.mutate({ name: name.trim() || "Untitled recording", channels }, {
      onSuccess: () => {
        setName("");
        listQuery.refetch();
      },
      onError: () => setError("Could not start — is the robot connected?"),
    });
  };

  const finish = () => stopMutation.mutate(undefined, { onSuccess: () => listQuery.refetch() });

  return (
    <div>
      {activeRow ? (
        <div className="rec-active">
          <span className="rec-dot" />
          Recording “{activeRow.name}” — {activeRow.sample_count} samples
          <div style={{ flex: 1 }} />
          <button className="btn danger" onClick={finish}>
            <Square size={13} /> Stop
          </button>
        </div>
      ) : (
        <div className="rec-start">
          <input
            className="text-input"
            style={{ margin: 0, flex: 1 }}
            placeholder="Recording name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && online) begin(); }}
          />
          <button className="btn primary" disabled={!online || start.isPending} onClick={begin}
                  title={online ? "Start recording" : "The robot is not connected"}>
            <Circle size={13} /> Record
          </button>
        </div>
      )}
      {!activeRow && (
        <div className="rec-channels">
          {CHANNELS.map((channel) => (
            <label key={channel.id} className="rec-channel">
              <input
                type="checkbox"
                checked={channels.includes(channel.id)}
                onChange={() => toggleChannel(channel.id)}
              />
              {channel.label}
            </label>
          ))}
          <label className="rec-channel disabled" title="Camera video recording is coming later">
            <input type="checkbox" disabled />
            Camera video (coming later)
          </label>
        </div>
      )}
      {error && <p className="subtext" style={{ color: "var(--danger)" }}>{error}</p>}

      {rows.filter((row) => row.status === "done").map((row) => (
        <div className="rec-row" key={row.id}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>{row.name}</div>
            <div className="subtext">
              {formatTime(iso(row.started_at))} · {durationLabel(row)} · {row.sample_count} samples
            </div>
          </div>
          <button className="btn icon" title="Replay in a new tab"
                  onClick={() => openReplayTab(row.id)}>
            <Play size={14} />
          </button>
          <DownloadMenu row={row} />
          <button className="btn icon" title="Delete recording"
                  onClick={() => remove.mutate(row.id, { onSuccess: () => listQuery.refetch() })}>
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      {rows.length === 0 && <p className="subtext">No recordings yet.</p>}
    </div>
  );
}
