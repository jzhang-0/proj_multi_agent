import { defineConfig, devices } from "@playwright/test";

const realWebBaseURL = process.env.AMUX_REAL_WEB_BASE_URL;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: realWebBaseURL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: realWebBaseURL ? undefined : {
    command: "npm run build && npm run serve",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
  projects: [{
    name: "chromium",
    use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
  }],
});
