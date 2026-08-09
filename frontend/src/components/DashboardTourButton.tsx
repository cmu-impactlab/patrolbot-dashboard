import { CircleHelp } from "lucide-react";
import { tourUrl } from "../lib/dashboardTour";
import { useUiStore } from "../stores/uiStore";

export function DashboardTourButton({ replay = false }: { replay?: boolean }) {
  const startTour = useUiStore((state) => state.startTour);

  if (replay) {
    return (
      <a
        className="btn"
        href={tourUrl()}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Open the guided dashboard tour in a new tab"
        title="Open the guided dashboard tour in a new tab"
      >
        <CircleHelp size={16} aria-hidden="true" /> Guided tour
      </a>
    );
  }

  return (
    <button
      className="btn icon"
      type="button"
      onClick={startTour}
      aria-label="Start the guided dashboard tour"
      title="Start the guided dashboard tour"
      data-tour="help"
    >
      <CircleHelp size={16} aria-hidden="true" />
    </button>
  );
}
