/** Query-string handoff for starting the dashboard tour from a replay tab. */

export function tourUrl(): string {
  const parameters = new URLSearchParams(location.search);
  parameters.delete("replay");
  parameters.delete("guide");
  parameters.set("tour", "1");
  const query = parameters.toString();
  return `${location.pathname}${query ? `?${query}` : ""}`;
}

export function shouldStartTourFromUrl(): boolean {
  const parameters = new URLSearchParams(location.search);
  return parameters.get("tour") === "1" || parameters.get("guide") === "1";
}

/** Remove the one-shot trigger without reloading or losing unrelated query
 * parameters. The tour itself remains open in application state. */
export function consumeTourUrl(): void {
  const url = new URL(location.href);
  url.searchParams.delete("tour");
  url.searchParams.delete("guide");
  history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
}
