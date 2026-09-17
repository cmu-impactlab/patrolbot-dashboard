import {
  AlertTriangle, Ban, Bell, Download, Gauge, Info, Pause, Play, SkipBack,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { fetchRecordingDetail } from "../api/queries";
import { formatTime } from "../lib/format";
import {
  REPLAY_SPEEDS, useReplayStore, type RecordingRow, type ReplayEvent,
} from "../stores/replayStore";
import { useUiStore } from "../stores/uiStore";
import { ReplayMap } from "../widgets/LiveMapWidget/ReplayMap";
import { DashboardTourButton } from "../components/DashboardTourButton";

/** SQLite datetime('now') → parseable ISO ("YYYY-MM-DD HH:MM:SS" is UTC). */
function iso(sqlite: string): string {
  return sqlite.includes("T") ? sqlite : sqlite.replace(" ", "T") + "Z";
}

function clock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const SEVERITY_ICON: Record<string, typeof Info> = {
  info: Info,
  warning: AlertTriangle,
  critical: Ban,
};

/** Re-renders on every animation frame while the replay is playing, so the
 *  transport, clock and event list track the map instead of lagging it. */
function usePlayhead(): number {
  const playing = useReplayStore((state) => state.playing);
  const tMs = useReplayStore((state) => state.tMs);
  const [t, setT] = useState(0);

  useEffect(() => {
    if (!playing) {
      setT(tMs); // paused or seeked: the store's value is the truth
      return;
    }
    let raf = 0;
    const tick = () => {
      const store = useReplayStore.getState();
      if (store.now() >= store.durationMs) {
        // Stop cleanly at the end rather than pinning against the max forever.
        store.pause();
        setT(store.durationMs);
        return;
      }
      setT(store.now());
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, tMs]);

  return t;
}

function Transport({ t }: { t: number }) {
  const playing = useReplayStore((state) => state.playing);
  const durationMs = useReplayStore((state) => state.durationMs);
  const speed = useReplayStore((state) => state.speed);
  const store = useReplayStore.getState();

  return (
    <div className="replay-transport">
      <button className="btn icon" title="Back to the start" onClick={() => store.seek(0)}>
        <SkipBack size={15} />
      </button>
      <button className="btn primary icon" title={playing ? "Pause" : "Play"}
              onClick={() => (playing ? store.pause() : store.play())}>
        {playing ? <Pause size={15} /> : <Play size={15} />}
      </button>
      <span className="replay-clock">{clock(t)}</span>
      <input
        type="range"
        aria-label="Replay position"
        min={0}
        max={Math.max(1, durationMs)}
        value={Math.round(Math.min(t, durationMs))}
        onChange={(event) => store.seek(Number(event.target.value))}
      />
      <span className="replay-clock muted">{clock(durationMs)}</span>
      <select className="replay-speed" value={speed} aria-label="Playback speed"
              onChange={(event) => store.setSpeed(Number(event.target.value))}>
        {REPLAY_SPEEDS.map((option) => (
          <option key={option} value={option}>{option}×</option>
        ))}
      </select>
    </div>
  );
}

function EventList({ t }: { t: number }) {
  const events = useReplayStore((state) => state.events);
  const store = useReplayStore.getState();
  if (events.length === 0) {
    return <p className="subtext">No alerts were captured in this recording.</p>;
  }
  return (
    <ol className="replay-events">
      {events.map((event: ReplayEvent, index: number) => {
        const Icon = SEVERITY_ICON[event.severity] ?? Info;
        const reached = event.tMs <= t;
        return (
          <li key={index} className={`replay-event ${event.severity} ${reached ? "" : "pending"}`}>
            <button onClick={() => store.seek(event.tMs)} title="Jump to this moment">
              <Icon size={14} />
              <span className="replay-event-time">{clock(event.tMs)}</span>
              <span className="replay-event-body">
                <strong>{event.title}</strong>
                {event.message && <span className="subtext">{event.message}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function Readout({ t }: { t: number }) {
  const poses = useReplayStore((state) => state.poses);
  const battery = useReplayStore((state) => state.battery);
  const store = useReplayStore.getState();
  const here = store.poseAt(t);
  const routeLength = useMemo(() => store.routeLength(), [poses]);

  // Most recent battery reading at or before the playhead.
  let charge: number | null = null;
  for (const reading of battery) {
    if (reading.tMs > t) break;
    if (reading.percentage != null) charge = reading.percentage;
  }

  return (
    <dl className="replay-readout">
      <div><dt>Position</dt>
        <dd>{here ? `${here.x.toFixed(2)}, ${here.y.toFixed(2)} m` : "—"}</dd></div>
      <div><dt>Heading</dt>
        <dd>{here ? `${((here.yaw * 180) / Math.PI).toFixed(0)}°` : "—"}</dd></div>
      <div><dt>Speed</dt>
        <dd>{here ? `${here.linearVelocity.toFixed(2)} m/s` : "—"}</dd></div>
      <div><dt>Battery</dt>
        <dd>{charge == null ? "not recorded" : `${charge.toFixed(0)}%`}</dd></div>
      <div><dt>Route length</dt><dd>{routeLength.toFixed(1)} m</dd></div>
      <div><dt>Positions</dt><dd>{poses.length}</dd></div>
    </dl>
  );
}

export function ReplayPage({ recordingId }: { recordingId: number }) {
  const theme = useUiStore((state) => state.theme);
  const recording = useReplayStore((state) => state.recording);
  const poses = useReplayStore((state) => state.poses);
  const [error, setError] = useState("");
  const t = usePlayhead();

  useEffect(() => {
    setError("");
    let cancelled = false;
    fetchRecordingDetail(recordingId)
      .then((detail) => {
        if (cancelled) return;
        const { samples, ...row } = detail;
        useReplayStore.getState().load(row as RecordingRow, samples);
      })
      .catch(() => { if (!cancelled) setError("That recording could not be loaded."); });
    return () => {
      cancelled = true;
      useReplayStore.getState().close();
    };
  }, [recordingId]);

  useEffect(() => {
    document.title = recording ? `Replay — ${recording.name}` : "Replay — PatrolBot";
  }, [recording]);

  // The transport is the whole point of this page, so space bar drives it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(target.tagName)) return;
      const store = useReplayStore.getState();
      if (event.code === "Space") {
        event.preventDefault();
        store.playing ? store.pause() : store.play();
      } else if (event.key === "ArrowLeft") {
        store.seek(Math.max(0, store.now() - 5000));
      } else if (event.key === "ArrowRight") {
        store.seek(Math.min(store.durationMs, store.now() + 5000));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) {
    return <div className="replay-page"><p className="subtext">{error}</p><a className="btn" href="/">Return to dashboard</a></div>;
  }
  if (!recording) {
    return <div className="replay-page" aria-busy="true"><p className="subtext">Loading replay…</p></div>;
  }

  return (
    <div className="replay-page">
      <header className="replay-header">
        <div>
          <h1>{recording.name}</h1>
          <p className="subtext">
            {recording.robot_id} · {formatTime(iso(recording.started_at))} ·{" "}
            {recording.sample_count} samples
          </p>
        </div>
        <a className="btn" href={`/api/recordings/${recording.id}/export.zip`} download>
          <Download size={14} /> Download data
        </a>
        <a className="btn" href="/">Return to dashboard</a>
        <DashboardTourButton replay />
      </header>

      <div className="replay-body">
        <div className="replay-stage">
          {poses.length === 0 ? (
            <div className="map-empty">
              This recording has no position data, so there is no route to replay.
              Record the “Position &amp; speed” channel to see the robot move here.
            </div>
          ) : (
            <ReplayMap theme={theme} />
          )}
          {poses.length > 0 && <Transport t={t} />}
        </div>
        <aside className="replay-side">
          <h2><Gauge size={14} /> At this moment</h2>
          <Readout t={t} />
          <h2><Bell size={14} /> Alerts</h2>
          <EventList t={t} />
        </aside>
      </div>
    </div>
  );
}
