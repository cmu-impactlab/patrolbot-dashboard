import { ManualDrive } from "../components/ManualDrive";
import { TuningPanel } from "../components/TuningPanel";
import { useTelemetrySocket } from "../websocket/useTelemetrySocket";
import { useTuningReadback } from "../lib/useTuningReadback";
import { SpeedGauges } from "../components/SpeedGauges";

/** AuthGate is the sole parent. This tab has its own fresh browser socket and
 * never loads a dashboard layout, saved command intent or another tab's lease. */
export function AdvancedPage() {
  useTelemetrySocket();
  return <main className="advanced-page">
    <header className="advanced-header">
      <div><h1>Advanced controls</h1><p>Saved targets and live robot values, side by side.</p></div>
      <a className="btn" href="/">Dashboard</a>
    </header>
    <div className="advanced-workspace">
      <TuningPanel />
      <AdvancedSidebar />
    </div>
  </main>;
}

function AdvancedSidebar() {
  const { observed, receivedAt } = useTuningReadback();
  return <aside className="advanced-side" aria-label="Speed and manual controls">
    <SpeedGauges limits={observed?.limits ?? null}
      limitsAt={observed && receivedAt !== null ? receivedAt - observed.ageS * 1000 : null} />
    <ManualDrive compact />
  </aside>;
}
