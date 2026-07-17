import { HEALTH_COPY, healthOverallText } from "../lib/plainLanguage";
import { useTelemetryStore } from "../stores/telemetryStore";

export function SystemHealthWidget() {
  const health = useTelemetryStore((state) => state.health);

  if (!health) {
    return <p className="subtext">Waiting for the first health report…</p>;
  }

  const attention = health.subsystems.filter((sub) => sub.level !== "healthy").length;
  const overallCopy = HEALTH_COPY[health.overall];

  return (
    <div>
      <div className={`health-overall tone-${overallCopy.tone}`}>
        <span className="dot" />
        {healthOverallText(health.overall, attention)}
      </div>
      {health.subsystems.map((sub) => (
        <div className="subsystem" key={sub.id}>
          <span className={`level-dot ${sub.level}`} />
          <div>
            <div className="name">{sub.label}</div>
            <div className="msg">{sub.message}</div>
            {sub.action && <div className="action">{sub.action}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}
