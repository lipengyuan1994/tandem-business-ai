// End-to-end tests against the local dev stack (`make dev`), task P3-16. Run with `make e2e`.
import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.(e2e|setup)\.ts$/,
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1, // one shared dev database and Keycloak; TOTP codes are single-use per period
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost:8443",
    ignoreHTTPSErrors: true, // Caddy's local development CA
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // No traces or screenshots while typing passwords and TOTP codes.
    { name: "login", testMatch: /auth\.setup\.ts$/, use: { ...devices["Desktop Chrome"], trace: "off", screenshot: "off" } },
    { name: "app", testMatch: /\.e2e\.ts$/, dependencies: ["login"], use: { ...devices["Desktop Chrome"] } },
  ],
})
