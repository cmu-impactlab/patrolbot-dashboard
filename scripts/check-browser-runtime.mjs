/** Fail early on image/lockfile drift or missing native libraries; never download. */
import { access } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const lock = require("./package-lock.json");
const expected = lock.packages["node_modules/playwright"].version;
const installed = require("playwright/package.json").version;
if (installed !== expected) {
  throw new Error(`Playwright ${installed} differs from lockfile ${expected}; run npm ci.`);
}

const { chromium, webkit } = require("playwright");
for (const [name, type] of [["chromium", chromium], ["webkit", webkit]]) {
  try {
    await access(type.executablePath());
  } catch (cause) {
    throw new Error(`Missing preinstalled ${name} for Playwright ${expected}. Update the pinned CI image to match frontend/package-lock.json.`, { cause });
  }
  const browser = await type.launch({ headless: true, timeout: 30_000 });
  try {
    const page = await browser.newPage();
    await page.setContent("<title>Browser runtime check</title>");
    if (await page.title() !== "Browser runtime check") throw new Error(`${name} did not render`);
    console.log(`${name}: Playwright ${installed}, browser ${browser.version()} — launch/render OK`);
  } finally {
    await browser.close();
  }
}
