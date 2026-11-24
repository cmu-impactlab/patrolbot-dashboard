import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchTuningSnapshot } from "../api/tuning";
import { BUNDLED_ENTRIES, normalizeSnapshot } from "../lib/tuning";
import { useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { useTuningStore } from "../stores/tuningStore";
import { TuningPanel } from "./TuningPanel";

vi.mock("../api/tuning", () => ({ fetchTuningSnapshot: vi.fn() }));
const entry = BUNDLED_ENTRIES.find(item => item.id === "amcl.alpha1")!;
const legacy = normalizeSnapshot({
  session_id: "11111111-1111-4111-8111-111111111111",
  desired_revision: 2, committed_active_revision: 1,
  writable: true, activation_supported: true, pending_keys: [entry.id],
  safe_apply: { allowed: true, reason: "" },
  desired: { [entry.id]: .08 }, catalog: [entry],
  readback: { source: "ros_parameters", age_s: .1, hardware_session_generation: 1, values: { [entry.id]: .05 } },
});

beforeEach(() => {
  useTuningStore.getState().connectionLost();
  useTuningStore.setState({ snapshot: null, receivedAt: null, status: "idle" });
  useTelemetryStore.setState({ wsConnected: true, robotId: "test-robot", baseState: { session_generation: 1 } as never });
  vi.mocked(fetchTuningSnapshot).mockResolvedValue(legacy);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it.each(["administrator", "observer"] as const)("renders live and configured values but no write controls for %s", async role => {
  useAuthStore.setState({ user: { id: 1, username: "fixture", display_name: "Fixture", role, auth_mode: "local" } });
  const { container } = render(<TuningPanel />);
  expect(await screen.findByText("0.05")).toBeTruthy();
  expect(screen.getByText("0.08")).toBeTruthy();
  expect(screen.getByText(/Read-only: inspect/)).toBeTruthy();
  expect(container.querySelector("input, select, textarea")).toBeNull();
  expect(screen.queryByRole("button", { name: /save|apply|redeploy|restore/i })).toBeNull();
  fireEvent.click(screen.getByLabelText(`Details for ${entry.label}`));
  expect(screen.getByText(/Configured target: 0.08/)).toBeTruthy();
  expect(screen.getByText(/Live readback: 0.05/)).toBeTruthy();
});
