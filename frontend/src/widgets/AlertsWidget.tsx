import { useState } from "react";
import { SEVERITY_COPY } from "../lib/plainLanguage";
import { formatTime } from "../lib/format";
import { useTelemetryStore } from "../stores/telemetryStore";
import type { EventSeverity } from "../types/protocol";

const FILTERS: ("all" | EventSeverity)[] = ["all", "critical", "warning", "info"];

export function AlertsWidget() {
  const events = useTelemetryStore((state) => state.events);
  const lastAcked = useTelemetryStore((state) => state.lastAckedEventId);
  const ackEvents = useTelemetryStore((state) => state.ackEvents);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");

  const visible = events.filter((event) => filter === "all" || event.severity === filter);
  const unread = events.filter((event) => event.id > lastAcked).length;

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
        {unread > 0 && (
          <button className="btn" style={{ padding: "3px 10px", fontSize: 12 }} onClick={ackEvents}>
            Mark {unread} read
          </button>
        )}
      </div>
      {visible.length === 0 && <p className="subtext">No alerts.</p>}
      {visible.map((event) => {
        const severity = SEVERITY_COPY[event.severity];
        return (
          <div className={`event-row ${event.id > lastAcked ? "unread" : ""}`} key={event.id}>
            <span className={`sev tone-${severity.tone}`}>{severity.label}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>{event.title}</div>
              <div className="subtext">{event.message}</div>
            </div>
            <span className="when">{formatTime(event.ts)}</span>
          </div>
        );
      })}
    </div>
  );
}
