import { ManualDrive } from "../components/ManualDrive";
import { TuningPanel } from "../components/TuningPanel";
import { useTelemetrySocket } from "../websocket/useTelemetrySocket";

/** AuthGate is the sole parent. This tab has its own fresh browser socket and
 * never loads a dashboard layout, saved command intent or another tab's lease. */
export function AdvancedPage() {
  useTelemetrySocket();
  return <main className="advanced-page">
    <h1>Advanced controls</h1>
    <TuningPanel />
    <ManualDrive />
  </main>;
}
