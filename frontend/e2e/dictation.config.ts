// Offline browser checks: use built assets and intercepted synthetic API responses.
// No dev stack, login/global setup, sockets, downloads, or model calls.
import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: ".",
  testMatch: "dictation.e2e.ts",
  workers: 1,
  timeout: 30_000,
  reporter: "list",
  outputDir: "../output/playwright/dictation",
  use: {
    ...devices["Desktop Chrome"],
    permissions: ["microphone"],
    launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
  },
})
