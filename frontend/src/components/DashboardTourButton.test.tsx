import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useUiStore } from "../stores/uiStore";
import { DashboardTourButton } from "./DashboardTourButton";

afterEach(() => {
  cleanup();
  history.replaceState({}, "", "/");
  useUiStore.setState({ tourActive: false, tourRequest: 0 });
});

describe("permanent tour access", () => {
  it("starts the tour directly from the dashboard", () => {
    render(<DashboardTourButton />);
    fireEvent.click(screen.getByRole("button", { name: "Start the guided dashboard tour" }));
    expect(useUiStore.getState().tourActive).toBe(true);
    expect(useUiStore.getState().tourRequest).toBe(1);
  });

  it("returns from replay to a new dashboard tour tab", () => {
    history.replaceState({}, "", "/?replay=12");
    render(<DashboardTourButton replay />);
    const link = screen.getByRole("link", { name: /Open the guided dashboard tour/ });
    expect(link.getAttribute("href")).toBe("/?tour=1");
    expect(link.getAttribute("target")).toBe("_blank");
  });
});
