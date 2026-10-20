import { normalizeSnapshot, parseTuningResponse } from "../lib/tuning";
import type { CanonicalRequest, NormalizedSnapshot, TuningJob, TuningResponse } from "../lib/tuning";

/**
 * HTTP client for the read/status/save/apply/redeploy/restore endpoints owned
 * by the API agent. The browser sends only canonical logical requests; the
 * server derives the actor and re-checks permissions on every mutation.
 */

export class TuningHttpError extends Error {
  status: number;
  reason: string | null;
  constructor(status: number, reason: string | null) {
    super(reason ? `${reason} (HTTP ${status})` : `Tuning request failed (HTTP ${status})`);
    this.name = "TuningHttpError";
    this.status = status;
    this.reason = reason;
  }
}

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
  let response: Response;
  try {
    response = await fetch(base(robotId), { headers: { Accept: "application/json" } });
  } catch {
    return normalizeSnapshot(null);
  }
  if (!response.ok) {
    const reason = await reasonFrom(response);
    const snapshot = normalizeSnapshot(null);
    return reason ? { ...snapshot, reason } : snapshot;
  }
  try {
    return normalizeSnapshot(await response.json());
  } catch {
    return normalizeSnapshot(null);
  }
}

/** POST one canonical request. Caller maps failures to visible statuses. */
export async function postTuningRequest(robotId: string, request: CanonicalRequest): Promise<TuningResponse> {
  const response = await fetch(base(robotId), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new TuningHttpError(response.status, await reasonFrom(response));
  return parseTuningResponse(await response.json());
}

/** GET a durable job by id; used to resolve an unknown outcome, never to retry it. */
export async function fetchTuningJob(robotId: string, jobId: string): Promise<TuningJob> {
  const response = await fetch(`${base(robotId)}/jobs/${encodeURIComponent(jobId)}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new TuningHttpError(response.status, await reasonFrom(response));
  const body = (await response.json()) as unknown;
  if (body && typeof body === "object" && "job" in body) {
    const job = (body as { job: unknown }).job;
    if (job && typeof job === "object") return job as TuningJob;
  }
  return (body && typeof body === "object" ? body : {}) as TuningJob;
}
