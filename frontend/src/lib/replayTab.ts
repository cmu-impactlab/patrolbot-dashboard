/**
 * The replay view lives at ?replay=<id> on the same page.
 *
 * A query parameter rather than a path: the server hands the built frontend to
 * the browser via StaticFiles, which has no SPA fallback, so /replay/7 would
 * 404 on a hard load or on a link someone pasted to a colleague.
 */

export function replayUrl(recordingId: number): string {
  return `${location.pathname}?replay=${recordingId}`;
}

/** The recording this page should replay, or null for the dashboard. */
export function replayIdFromUrl(): number | null {
  const raw = new URLSearchParams(location.search).get("replay");
  if (raw === null) return null;
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** Open a recording's replay in its own tab, reusing the tab per recording so
 *  clicking play twice does not pile up duplicates. */
export function openReplayTab(recordingId: number): void {
  window.open(replayUrl(recordingId), `patrolbot-replay-${recordingId}`);
}
