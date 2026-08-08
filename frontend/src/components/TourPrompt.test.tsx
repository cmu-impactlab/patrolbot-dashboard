import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/uiStore";
import { TourPrompt } from "./TourPrompt";

function response(body: unknown, ok = true): Response {
  return { ok, status: ok ? 200 : 503, json: async () => body } as Response;
}

function renderPrompt(fetchImpl: typeof fetch, onResolved = vi.fn()) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(<QueryClientProvider client={client}><TourPrompt onResolved={onResolved} /></QueryClientProvider>),
    client,
    onResolved,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useUiStore.setState({ tourActive: false, tourRequest: 0 });
});

describe("first-visit tour offer", () => {
  it("starts the tour on this dashboard and acknowledges the account", async () => {
    const calls: string[] = [];
    const { onResolved } = renderPrompt(async (_input, init) => {
      if (init?.method === "PUT") calls.push("put");
      return response(init?.method === "PUT"
        ? { current_version: 1, seen_version: 1, should_prompt: false }
        : { current_version: 1, seen_version: 0, should_prompt: true });
    });
    fireEvent.click(await screen.findByRole("button", { name: "Start guided tour" }));
    expect(useUiStore.getState().tourActive).toBe(true);
    expect(onResolved).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(calls).toEqual(["put"]));
  });

  it("Not now acknowledges without starting", async () => {
    const { onResolved } = renderPrompt(async (_input, init) => response(
      init?.method === "PUT"
        ? { current_version: 1, seen_version: 1, should_prompt: false }
        : { current_version: 1, seen_version: 0, should_prompt: true },
    ));
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(useUiStore.getState().tourActive).toBe(false);
    expect(onResolved).toHaveBeenCalledTimes(1);
  });

  it("closes immediately even when acknowledgement must retry", async () => {
    const { onResolved, client } = renderPrompt(async (_input, init) =>
      init?.method === "PUT"
        ? response({}, false)
        : response({ current_version: 1, seen_version: 0, should_prompt: true }));
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onResolved).toHaveBeenCalledTimes(1);
    client.clear();
  });

  it("does not stack the offer over a tour started from the help button", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input, init?: RequestInit) => response(
      init?.method === "PUT"
        ? { current_version: 1, seen_version: 1, should_prompt: false }
        : { current_version: 1, seen_version: 0, should_prompt: true },
    )));
    const onResolved = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <TourPrompt onResolved={onResolved} tourAlreadyStarted />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("heading", { name: /Take a quick dashboard tour/ })).toBeNull();
  });
});
