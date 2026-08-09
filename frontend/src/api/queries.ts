import { useMutation, useQuery } from "@tanstack/react-query";
import type { MapData } from "../types/protocol";
import type { LayoutDoc, SavedLayout } from "../stores/layoutStore";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.status === 401) {
    // OIDC deployments: an expired/missing session sends the browser to log in.
    location.href = "/auth/login";
  }
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return (await response.json()) as T;
}

export interface TourStatus {
  current_version: number;
  seen_version: number;
  should_prompt: boolean;
}

export function useTourStatus() {
  return useQuery({
    queryKey: ["dashboard-tour-status"],
    queryFn: () => json<TourStatus>("/api/help-guide/status"),
    staleTime: Infinity,
  });
}

export function useAcknowledgeTour() {
  return useMutation({
    mutationFn: () => json<TourStatus>("/api/help-guide/status", { method: "PUT" }),
    // The prompt is dismissed immediately. Brief server/network interruptions
    // must not trap the user in onboarding, but should still converge on the
    // account-wide acknowledgement without another click.
    retry: 4,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
  });
}

export function useLayoutsQuery() {
  return useQuery({
    queryKey: ["layouts"],
    queryFn: () => json<SavedLayout[]>("/api/layouts"),
    staleTime: Infinity,
  });
}

export function useSaveLayout() {
  return useMutation({
    mutationFn: (doc: LayoutDoc) =>
      json("/api/layouts/current", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      }),
  });
}

/** Save the current layout under a user-chosen name (a custom dashboard). */
export function useSaveNamedLayout() {
  return useMutation({
    mutationFn: ({ name, doc }: { name: string; doc: LayoutDoc }) =>
      json(`/api/layouts/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(doc),
      }),
  });
}

export function useDeleteLayout() {
  return useMutation({
    mutationFn: (name: string) =>
      json(`/api/layouts/${encodeURIComponent(name)}`, { method: "DELETE" }),
  });
}

export function useMapQuery(mapVersion: number) {
  return useQuery({
    // Version 0 still fetches: the server falls back to its local static map
    // when no robot has streamed one (the real robot never transmits its map).
    queryKey: ["map", mapVersion],
    queryFn: () => json<MapData>("/api/map"),
    staleTime: Infinity,
    retry: 2,
  });
}

import type { RecordingRow, RecordingSample } from "../stores/replayStore";

export function useRecordings() {
  return useQuery({
    queryKey: ["recordings"],
    queryFn: () => json<RecordingRow[]>("/api/recordings"),
    refetchInterval: 10_000,
  });
}

export function useStartRecording() {
  return useMutation({
    mutationFn: ({ name, channels }: { name: string; channels: string[] }) =>
      json<RecordingRow>("/api/recordings/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, channels }),
      }),
  });
}

export function useStopRecording() {
  return useMutation({
    mutationFn: () => json<RecordingRow>("/api/recordings/stop", { method: "POST" }),
  });
}

export function useDeleteRecording() {
  return useMutation({
    mutationFn: (id: number) => json(`/api/recordings/${id}`, { method: "DELETE" }),
  });
}

export function fetchRecordingDetail(id: number) {
  return json<RecordingRow & { samples: RecordingSample[] }>(`/api/recordings/${id}`);
}

export interface BatteryHistoryRow {
  ts: string;
  voltage: number;
  percentage: number | null;
  current: number | null;
  charging: number;
}

export function useBatteryHistory(minutes = 120) {
  return useQuery({
    queryKey: ["batteryHistory", minutes],
    queryFn: () => json<BatteryHistoryRow[]>(`/api/history/battery?minutes=${minutes}`),
    refetchInterval: 30_000,
  });
}
