import { Circle, Download, Pause, Play, Square, Trash2, X } from "lucide-react";
import { useState } from "react";
import {
  fetchRecordingDetail,
  useDeleteRecording,
  useRecordings,
  useStartRecording,
  useStopRecording,
} from "../api/queries";
import { useReplayStore, type RecordingRow } from "../stores/replayStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { formatTime } from "../lib/format";

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

function ReplayBar() {
  const recording = useReplayStore((state) => state.recording);
  const playing = useReplayStore((state) => state.playing);
  const durationMs = useReplayStore((state) => state.durationMs);
  // Subscribe to tMs so pausing re-renders with the frozen position.
  useReplayStore((state) => state.tMs);
  const store = useReplayStore.getState();
  if (!recording) return null;
  const t = store.now();

  return (
    <div className="replay-bar">
      <button className="btn icon" onClick={() => (playing ? store.pause() : store.play())}
              title={playing ? "Pause replay" : "Play replay"}>
        {playing ? <Pause size={14} /> : <Play size={14} />}
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(1, durationMs)}
        value={Math.round(t)}
        onChange={(event) => store.seek(Number(event.target.value))}
      />
      <span className="subtext" style={{ whiteSpace: "nowrap" }}>
        {(t / 1000).toFixed(0)}/{(durationMs / 1000).toFixed(0)} s
      </span>
      <button className="btn icon" onClick={store.close} title="Close replay">
        <X size={14} />
      </button>
    </div>
  );
}

export function RecordingsWidget() {
  const listQuery = useRecordings();
  const start = useStartRecording();
  const stopMutation = useStopRecording();
  const remove = useDeleteRecording();
  const online = useTelemetryStore((state) => state.connection.state === "online");
  const replayingId = useReplayStore((state) => state.recording?.id ?? null);
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

  const play = async (row: RecordingRow) => {
    const detail = await fetchRecordingDetail(row.id);
    useReplayStore.getState().load(row, detail.samples);
  };

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

      <ReplayBar />

      {rows.filter((row) => row.status === "done").map((row) => (
        <div className={`rec-row ${replayingId === row.id ? "replaying" : ""}`} key={row.id}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>{row.name}</div>
            <div className="subtext">
              {formatTime(iso(row.started_at))} · {durationLabel(row)} · {row.sample_count} samples
            </div>
          </div>
          <button className="btn icon" title="Replay on the Live Map" onClick={() => play(row)}>
            <Play size={14} />
          </button>
          <a className="btn icon" title="Download CSV" href={`/api/recordings/${row.id}/export.csv`} download>
            <Download size={14} />
          </a>
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
