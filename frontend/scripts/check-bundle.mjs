/**
 * Bundle budget for the production build.
 *
 * The number that matters is not the total build output — it is the static
 * JavaScript graph a browser must run before the dashboard appears: the entry
 * chunk plus everything it imports synchronously. Lazily imported chunks (the
 * replay view, the charting widgets) are reported but not counted, because the
 * shell renders without them. Note that this is deliberately not "everything
 * the first screen transfers": the default layout includes a chart widget, so
 * a first visit does usually fetch the charts chunk too — just not before the
 * dashboard is on screen. CSS is likewise excluded.
 *
 * Run after `vite build`: `npm run check-bundle`.
 */
import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Raise these deliberately, with a reason in the commit message. They are a
// ratchet against drift, not a target to grow into.
const BUDGET_KB = { initialStaticJsGzip: 175, anyChunkRaw: 500 };

const DIST = new URL("../dist/", import.meta.url).pathname;
const manifest = JSON.parse(readFileSync(join(DIST, ".vite/manifest.json"), "utf8"));

const gzipKb = (file) =>
  gzipSync(readFileSync(join(DIST, file))).length / 1024;
const rawKb = (file) => readFileSync(join(DIST, file)).length / 1024;

/** The entry and everything it pulls in synchronously. */
function initialChunks() {
  const entries = Object.keys(manifest).filter((key) => manifest[key].isEntry);
  // One entry today. If that ever changes, budgeting only the first would
  // leave the others unchecked forever, so stop rather than guess.
  if (entries.length !== 1) {
    throw new Error(`expected exactly one entry chunk, found ${entries.length}`
      + " — teach this script which ones to budget");
  }
  const seen = new Set();
  const walk = (key) => {
    if (seen.has(key)) return;
    seen.add(key);
    for (const imported of manifest[key]?.imports ?? []) walk(imported);
  };
  walk(entries[0]);
  return [...seen].map((key) => manifest[key].file);
}

const initial = initialChunks();
const lazy = Object.values(manifest)
  .map((chunk) => chunk.file)
  .filter((file) => file.endsWith(".js") && !initial.includes(file));

const initialGzip = initial.reduce((sum, file) => sum + gzipKb(file), 0);
const failures = [];

console.log("Initial static JS (entry + synchronous imports):");
for (const file of initial.sort((a, b) => gzipKb(b) - gzipKb(a))) {
  console.log(`  ${gzipKb(file).toFixed(1).padStart(7)} kB gzip  ${file}`);
}
console.log(`  ${initialGzip.toFixed(1).padStart(7)} kB gzip  TOTAL `
  + `(budget ${BUDGET_KB.initialStaticJsGzip} kB)`);

if (lazy.length) {
  console.log("\nLoaded on demand, not counted:");
  for (const file of lazy.sort((a, b) => gzipKb(b) - gzipKb(a))) {
    console.log(`  ${gzipKb(file).toFixed(1).padStart(7)} kB gzip  ${file}`);
  }
}

if (initialGzip > BUDGET_KB.initialStaticJsGzip) {
  failures.push(`initial static JS ${initialGzip.toFixed(1)} kB gzip exceeds `
    + `${BUDGET_KB.initialStaticJsGzip} kB`);
}
for (const chunk of Object.values(manifest)) {
  if (chunk.file.endsWith(".js") && rawKb(chunk.file) > BUDGET_KB.anyChunkRaw) {
    failures.push(`${chunk.file} is ${rawKb(chunk.file).toFixed(1)} kB raw, over `
      + `the ${BUDGET_KB.anyChunkRaw} kB single-chunk limit`);
  }
}

if (failures.length) {
  console.error("\nBundle budget exceeded:");
  for (const failure of failures) console.error(`  - ${failure}`);
  console.error("\nSplit the growth out, or raise the budget deliberately in "
    + "frontend/scripts/check-bundle.mjs.");
  process.exit(1);
}
console.log("\nWithin budget.");
