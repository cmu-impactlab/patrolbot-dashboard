import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./integration",
  outputDir: "./test-results-mock",
  workers: 1,
  timeout: 45_000,
  use: {
    ...devices["Pixel 7"], baseURL: "http://127.0.0.1:8127", trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  webServer: { command: "python integration/serve_mock.py", url: "http://127.0.0.1:8127/api/health", reuseExistingServer: false },
});
