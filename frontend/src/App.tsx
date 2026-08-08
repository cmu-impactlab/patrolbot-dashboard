import { useEffect, useState } from "react";
import { useLayoutsQuery } from "./api/queries";
import { AuthGate } from "./components/AuthGate";
import { DashboardGrid } from "./components/DashboardGrid";
import { DashboardTour } from "./components/DashboardTour";
import { RestorePosePrompt } from "./components/RestorePosePrompt";
import { TourPrompt } from "./components/TourPrompt";
import { TopBar } from "./components/TopBar";
import { consumeTourUrl, shouldStartTourFromUrl } from "./lib/dashboardTour";
import { replayIdFromUrl } from "./lib/replayTab";
import { ReplayPage } from "./pages/ReplayPage";
import { useLayoutStore, type SavedLayout } from "./stores/layoutStore";
import { useUiStore } from "./stores/uiStore";
import { useTelemetrySocket } from "./websocket/useTelemetrySocket";

function Dashboard() {
  useTelemetrySocket();
  const layoutsQuery = useLayoutsQuery();
  const hydrate = useLayoutStore((state) => state.hydrate);
  const [helpOfferResolved, setHelpOfferResolved] = useState(false);
  const startTour = useUiStore((state) => state.startTour);
  const tourActive = useUiStore((state) => state.tourActive);

  useEffect(() => {
    if (!shouldStartTourFromUrl()) return;
    consumeTourUrl();
    startTour();
  }, [startTour]);

  useEffect(() => {
    if (!layoutsQuery.data) return;
    const saved: SavedLayout[] = layoutsQuery.data;
    const current = saved.find((layout) => layout.name === "current" && !layout.is_preset);
    hydrate(saved, current?.layout ?? null);
  }, [layoutsQuery.data, hydrate]);

  return (
    <div className="app-shell">
      <TopBar />
      <div className="dashboard-scroll" data-tour="dashboard">
        <DashboardGrid />
      </div>
      <TourPrompt
        onResolved={() => setHelpOfferResolved(true)}
        tourAlreadyStarted={tourActive}
      />
      <DashboardTour />
      <RestorePosePrompt enabled={helpOfferResolved && !tourActive} />
    </div>
  );
}

export default function App() {
  const tourRequested = shouldStartTourFromUrl();
  const replayId = tourRequested ? null : replayIdFromUrl();
  return (
    <AuthGate>
      {replayId === null ? <Dashboard /> : <ReplayPage recordingId={replayId} />}
    </AuthGate>
  );
}
