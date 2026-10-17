import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { TopBar } from "./components/TopBar";
import { useAuthStore } from "./stores/authStore";
import { useTelemetryStore } from "./stores/telemetryStore";
import { useTelemetrySocket } from "./websocket/useTelemetrySocket";

vi.mock("./websocket/useTelemetrySocket", () => ({ useTelemetrySocket: vi.fn() }));
const operator = { id: 1, username: "op", display_name: "Operator", role: "operator", auth_mode: "local" as const };
const originalCheck = useAuthStore.getState().check;

describe("Advance profile link and authenticated route", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/?advanced=1");
    useAuthStore.setState({ user: operator, check: vi.fn(async () => {}) });
    useTelemetryStore.setState({ wsConnected: false, capabilities: [], baseState: null, baseStateAt: null });
    vi.clearAllMocks();
  });
  afterEach(() => { cleanup(); useAuthStore.setState({ check: originalCheck }); window.history.replaceState(null, "", "/"); });

  it("profile menu exposes exact Advance label and a safe new-tab link in local authentication mode", async () => {
    render(<QueryClientProvider client={new QueryClient()}><TopBar /></QueryClientProvider>);
    fireEvent.keyDown(screen.getByRole("button", { name: "User profile" }), { key: "Enter" });
    const link = await screen.findByRole("menuitem", { name: "Advance" });
    expect(link.getAttribute("href")).toBe("/?advanced=1");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(useTelemetrySocket).not.toHaveBeenCalled();
  });
  it("advanced route has one socket hook and only manual controls, without layout hydration", async () => {
    await act(async () => { render(<App />); });
    expect(await screen.findByRole("heading", { name: "Advanced controls" })).toBeTruthy();
    expect(useTelemetrySocket).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Not armed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Set Robot Location" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn motors on" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send Robot Here" })).toBeNull();
  });
  it("does not mount a socket or commands for an anonymous user", () => {
    useAuthStore.setState({ user: null }); render(<App />);
    expect(screen.getByRole("link", { name: "Sign in with your Andrew ID" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Advanced controls" })).toBeNull();
    expect(useTelemetrySocket).not.toHaveBeenCalled();
  });
  it("read-only users see the page and explanation but cannot arm", async () => {
    useAuthStore.setState({ user: { ...operator, role: "observer" } });
    await act(async () => { render(<App />); });
    expect(await screen.findByRole("heading", { name: "Advanced controls" })).toBeTruthy();
    expect(screen.getByText(/An operator account is required/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Arm manual drive" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
