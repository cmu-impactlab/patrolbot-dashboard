import { useEffect } from "react";
import { useLayoutsQuery } from "./api/queries";
import { DashboardGrid } from "./components/DashboardGrid";
import { TopBar } from "./components/TopBar";
import { useLayoutStore, type SavedLayout } from "./stores/layoutStore";
import { useTelemetrySocket } from "./websocket/useTelemetrySocket";

export default function App() {
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
    </div>
  );
}
