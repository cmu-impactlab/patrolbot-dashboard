import { normalizeSnapshot, type NormalizedSnapshot } from "../lib/tuning";

async function reasonFrom(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as unknown;
    if (body && typeof body === "object" && "reason" in body && typeof (body as { reason: unknown }).reason === "string") {
      return (body as { reason: string }).reason;
    }
    if (body && typeof body === "object" && "detail" in body && typeof (body as { detail: unknown }).detail === "string") {
      return (body as { detail: string }).detail;
    }
  } catch {
    // Not JSON; fall through to null.
  }
  return null;
}

const base = (robotId: string) => `/api/robots/${encodeURIComponent(robotId)}/tuning`;

/** GET runtime snapshot. Unavailable (503) yields an honest no-snapshot result
 *  that still carries the server's precise reason. */
export async function fetchTuningSnapshot(robotId: string): Promise<NormalizedSnapshot> {
  const started = performance.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
  let response: Response;
  try {
    response = await fetch(base(robotId), { headers: { Accept: "application/json" }, signal: controller.signal });
  } catch {
    return normalizeSnapshot(null);
  }
  if (!response.ok) {
    const reason = await reasonFrom(response);
    const snapshot = normalizeSnapshot(null);
    return reason ? { ...snapshot, reason } : snapshot;
  }
  try {
    const snapshot = normalizeSnapshot(await response.json());
    // Bound observation age conservatively by the complete HTTP round-trip.
    return snapshot.readback ? { ...snapshot, readback: { ...snapshot.readback,
      ageS: snapshot.readback.ageS + Math.max(0, performance.now() - started) / 1000 } } : snapshot;
  } catch {
    return normalizeSnapshot(null);
  }
  } finally {
    clearTimeout(timeout);
  }
}
