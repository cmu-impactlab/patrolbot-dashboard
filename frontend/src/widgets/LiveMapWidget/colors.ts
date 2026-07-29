/**
 * Theme colours for the map canvases, cached per theme.
 *
 * getComputedStyle forces a style resolution, and a draw pass asks for ~11
 * colours per frame. Uncached that was ~660 forced resolutions a second, which
 * is enough to make the whole tab feel sluggish. The cache key is the theme
 * attribute — a plain attribute read, no style resolution — so a theme switch
 * still repaints in the new colours.
 */
let cache: { theme: string; values: Map<string, string> } = { theme: "", values: new Map() };

export function cssVar(name: string): string {
  const theme = document.documentElement.dataset.theme ?? "";
  if (cache.theme !== theme) cache = { theme, values: new Map() };
  const cached = cache.values.get(name);
  if (cached !== undefined) return cached;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  cache.values.set(name, value);
  return value;
}
