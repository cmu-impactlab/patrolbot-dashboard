import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useAuthStore } from "../stores/authStore";
import { useTelemetryStore } from "../stores/telemetryStore";
import { RestorePosePrompt } from "./RestorePosePrompt";

beforeEach(() => {
  useAuthStore.setState({ user: { id: 1, username: "test", display_name: "Test", role: "operator", auth_mode: "local" } });
  useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, map_id: "cmuq-floor2", map_revision: "test-revision",  },
    connection: { state: "online", last_seen: new Date().toISOString() },
    lastKnownPose: { map_id: "cmuq-floor2", map_revision: "test-revision",  x: 1, y: 2, yaw: 0 },
    poseSetThisSession: false,
  });
});

afterEach(() => {
  cleanup();
  useTelemetryStore.setState({ baseState: { ...useTelemetryStore.getState().baseState!, map_id: "cmuq-floor2", map_revision: "test-revision",  },
    connection: { state: "offline", last_seen: null },
    lastKnownPose: null,
    poseSetThisSession: false,
  });
});

describe("restore-location prompt ordering", () => {
  it("waits until help onboarding has resolved", () => {
    const { rerender } = render(<RestorePosePrompt enabled={false} />);
    expect(screen.queryByRole("heading", { name: /Continue from where/ })).toBeNull();
    rerender(<RestorePosePrompt enabled />);
    expect(screen.getByRole("heading", { name: /Continue from where/ })).toBeTruthy();
  });
});
