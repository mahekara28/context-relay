import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "packages/relay/test",
  testMatch: "workspace.spec.mjs",
  workers: 1,
  fullyParallel: false,
  timeout: 30000,
  use: {
    headless: true,
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.RELAY_CHROME
      ? { executablePath: process.env.RELAY_CHROME }
      : {},
  },
  reporter: "list",
});
