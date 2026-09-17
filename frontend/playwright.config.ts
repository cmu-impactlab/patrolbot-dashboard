import { defineConfig, devices } from "@playwright/test";

const chromiumLaunch = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {};
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  workers: 3,
  timeout: 45_000,
  expect: { timeout: 7_000 },
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  projects: [
    { name: "android-phone", use: { ...devices["Pixel 7"], launchOptions: chromiumLaunch } },
    { name: "android-tablet", use: { ...devices["Pixel 7"], viewport: { width: 1024, height: 768 }, launchOptions: chromiumLaunch } },
    { name: "iphone", use: { ...devices["iPhone 13"] } },
    { name: "ipad", use: { ...devices["iPad (gen 7)"] } },
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], launchOptions: chromiumLaunch } },
    { name: "desktop-webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173", reuseExistingServer: false,
  },
});
