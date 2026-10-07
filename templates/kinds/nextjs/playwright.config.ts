import { defineConfig, devices } from "@playwright/test";

// End to end against the built app (`npm run build` first) and a real Postgres at DATABASE_URL.
const port = 3100;
const databaseUrl = process.env.DATABASE_URL ?? "postgres://postgres:postgres@localhost:5432/app";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://localhost:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run db:migrate && npm run start -- --port ${port}`,
    url: `http://localhost:${port}/api/health`,
    reuseExistingServer: !process.env.CI,
    env: {
      DATABASE_URL: databaseUrl,
      BETTER_AUTH_SECRET: process.env.BETTER_AUTH_SECRET ?? "e2e-only-secret-not-for-production-use",
      BETTER_AUTH_URL: `http://localhost:${port}`,
    },
  },
});
