import { Anchor, Crosshair, MapPin, Octagon } from "lucide-react";

/**
 * Rendered but intentionally disabled: motion commands are Phase 3. There is
 * deliberately no mock-only command path — the widget must never teach users
 * interactions the real robot doesn't support yet.
 */
export function NavControlsWidget() {
  return (
    <div>
      <div className="nav-disabled-banner">
        Motion commands arrive in Phase 3. This dashboard is currently view-only.
      </div>
      <div className="nav-buttons">
        <button className="btn wide primary" disabled title="Available in Phase 3">
          <MapPin size={15} /> Send Robot Here
        </button>
        <button className="btn" disabled title="Available in Phase 3">
          <Crosshair size={15} /> Set Robot Location
        </button>
        <button className="btn" disabled title="Available in Phase 3">
          <Anchor size={15} /> Return to Dock
        </button>
        <button className="btn wide" disabled title="Available in Phase 3">
          <Octagon size={15} /> Stop Robot
        </button>
      </div>
      <p className="subtext" style={{ marginTop: 10 }}>
        In an actual emergency always use the red physical emergency-stop button on the robot —
        never rely on a software button.
      </p>
    </div>
  );
}
