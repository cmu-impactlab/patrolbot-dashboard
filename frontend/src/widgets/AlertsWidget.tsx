import { Check, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { SEVERITY_COPY } from "../lib/plainLanguage";
import { formatTime } from "../lib/format";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { EventData, EventSeverity } from "../types/protocol";

const FILTERS: ("all" | EventSeverity)[] = ["all", "critical", "warning", "info"];

function AlertRow({ event, seen, onSeen }: {
  event: EventData;
  seen: boolean;
  onSeen?: () => void;
}) {
  const severity = SEVERITY_COPY[event.severity];
  return (
    <div className={`event-row ${seen ? "seen" : "unread"}`}>
      <span className={`sev tone-${severity.tone}`}>{severity.label}</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600 }}>{event.title}</div>
        <div className="subtext">{event.message}</div>
      </div>
      <span className="when">{formatTime(event.ts)}</span>
      {onSeen && (
        <button className="event-dismiss" title="Mark as read" onClick={onSeen}>
          <Check size={14} />
        </button>
      )}
    </div>
  );
}

export function AlertsWidget() {
  const events = useTelemetryStore((state) => state.events);
  const seenIds = useTelemetryStore((state) => state.seenEventIds);
  const markSeen = useTelemetryStore((state) => state.markSeen);
  const markAllSeen = useTelemetryStore((state) => state.markAllSeen);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [showSeen, setShowSeen] = useState(false);

  const matches = events.filter((event) => filter === "all" || event.severity === filter);
  const unread = matches.filter((event) => !seenIds.includes(event.id));
  const seen = matches.filter((event) => seenIds.includes(event.id));

  return (
    <div>
      <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
        {FILTERS.map((option) => (
          <button
            key={option}
            className={`btn ${filter === option ? "active" : ""}`}
            style={{ padding: "3px 10px", fontSize: 12 }}
            onClick={() => setFilter(option)}
          >
            {option === "all" ? "All" : SEVERITY_COPY[option].label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        {unread.length > 0 && (
          <button className="btn" style={{ padding: "3px 10px", fontSize: 12 }} onClick={markAllSeen}>
            Mark all read
          </button>
        )}
      </div>

      {unread.length === 0 && <p className="subtext">No new alerts.</p>}
      {unread.map((event) => (
        <AlertRow key={event.id} event={event} seen={false} onSeen={() => markSeen(event.id)} />
      ))}

      {seen.length > 0 && (
        <>
          <button className="seen-toggle" onClick={() => setShowSeen(!showSeen)}>
            {showSeen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            Seen ({seen.length})
          </button>
          {showSeen && seen.map((event) => (
            <AlertRow key={event.id} event={event} seen />
          ))}
        </>
      )}
    </div>
  );
}
