import { useEffect } from "react";
import { useLayoutsQuery } from "./api/queries";
import { AuthGate } from "./components/AuthGate";
import { DashboardGrid } from "./components/DashboardGrid";
import { RestorePosePrompt } from "./components/RestorePosePrompt";
import { TopBar } from "./components/TopBar";
import { useLayoutStore, type SavedLayout } from "./stores/layoutStore";
import { useTelemetrySocket } from "./websocket/useTelemetrySocket";

function Dashboard() {
  useTelemetrySocket();
  const layoutsQuery = useLayoutsQuery();
  const hydrate = useLayoutStore((state) => state.hydrate);

  useEffect(() => {
    if (!layoutsQuery.data) return;
    const saved: SavedLayout[] = layoutsQuery.data;
    const current = saved.find((layout) => layout.name === "current" && !layout.is_preset);
    hydrate(saved, current?.layout ?? null);
  }, [layoutsQuery.data, hydrate]);

  return (
    <div className="app-shell">
      <TopBar />
      <div className="dashboard-scroll">
        <DashboardGrid />
      </div>
      <RestorePosePrompt />
    </div>
  );
}

export default function App() {
  return (
    <AuthGate>
      <Dashboard />
    </AuthGate>
  );
}
