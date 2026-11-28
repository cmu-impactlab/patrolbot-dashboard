import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecordingsWidget } from "./RecordingsWidget";

const { query, stop } = vi.hoisted(() => ({
  query: { data: [] as unknown[], refetch: vi.fn(), isError: false },
  stop: { mutate: vi.fn(), isPending: false, isError: false },
}));
vi.mock("../api/queries", () => ({
  useRecordings: () => query,
  useStopRecording: () => stop,
  useStartRecording: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRecording: () => ({ mutate: vi.fn(), isError: false }),
}));
vi.mock("../stores/authStore", () => ({
  useAuthStore: () => ({}), canCommand: () => true, isAdministrator: () => true,
}));

const row = {
  id: 4, robot_id: "r", name: "Patrol", channels: [], status: "done",
  started_at: "2026-11-28 00:00:00", ended_at: "2026-11-28 00:01:00", sample_count: 100000,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("recording finalization", () => {
  it("shows the saved cap reason and keeps full export", () => {
    query.data = [{ ...row, stop_reason: "sample_limit" }];
    render(<RecordingsWidget />);
    expect(screen.getByText(/Reached the 100000-sample limit/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download data (.zip of CSVs)" }).getAttribute("href"))
      .toBe("/api/recordings/4/export.zip");
  });

  it.each(["sample_limit_with_loss", "write_error", "queue_full", "restart"])("discloses incomplete data for %s", (stop_reason) => {
    query.data = [{ ...row, stop_reason }];
    render(<RecordingsWidget />);
    expect(screen.getByText(/recorded data may be incomplete/)).toBeTruthy();
  });

  it("blocks a new recording while finishing", () => {
    query.data = [{ ...row, status: "stopping" }];
    render(<RecordingsWidget />);
    expect(screen.getByText("Finishing “Patrol”…")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Stop" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Record" })).toBeNull();
  });

  it("offers a stop retry after failed finalization", () => {
    query.data = [{ ...row, status: "stop_failed" }];
    render(<RecordingsWidget />);
    expect(screen.getByText(/Could not finalize/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry Stop" }));
    expect(stop.mutate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Record" })).toBeNull();
  });
});
