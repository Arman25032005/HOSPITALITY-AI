import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

// Use the pre-installed Chromium when present (sandboxed CI images); otherwise Playwright's own.
const PREINSTALLED = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const executablePath = process.env.CHROMIUM_PATH ?? (existsSync(PREINSTALLED) ? PREINSTALLED : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "reports/e2e-results.json" }]],
  use: {
    baseURL: process.env.E2E_WEB ?? "http://localhost:3301",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath,
      args: ["--no-sandbox"],
    },
  },
  outputDir: "reports/e2e-artifacts",
});
