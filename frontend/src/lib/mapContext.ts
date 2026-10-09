import type { MapContext } from "../types/protocol";

export function sameMap(a: MapContext | null | undefined, b: MapContext | null | undefined): boolean {
  return !!a?.map_id && !!a.map_revision && a.map_id === b?.map_id && a.map_revision === b.map_revision;
}
