import { useMutation, useQuery } from "@tanstack/react-query";
import type { MapData } from "../types/protocol";
import type { LayoutDoc, SavedLayout } from "../stores/layoutStore";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return (await response.json()) as T;
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

export function useMapQuery(mapVersion: number) {
  return useQuery({
    queryKey: ["map", mapVersion],
    queryFn: () => json<MapData>("/api/map"),
    enabled: mapVersion > 0,
    staleTime: Infinity,
    retry: 2,
  });
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
