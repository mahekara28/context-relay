import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "site/test",
  workers: 2,
  timeout: 30_000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173/context-relay/",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    colorScheme: "light",
    launchOptions: process.env.RELAY_CHROME
      ? { executablePath: process.env.RELAY_CHROME }
      : {},
  },
  webServer: {
    command: "npm run site:preview",
    url: "http://127.0.0.1:4173/context-relay/",
    reuseExistingServer: !process.env.CI,
  },
});
