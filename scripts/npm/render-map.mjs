/** Capture the current thread map with synthetic sessions only. No harness or model calls. */
import { chromium } from "playwright";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { startService } from "../../dist/server.js";
import { RelayClient } from "../../dist/client.js";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = join(root, "docs/assets/workspace");
const dir = mkdtempSync(join(tmpdir(), "relay-map-artwork-"));
const projectRoot = join(dir, "sample-project");
mkdirSync(projectRoot);
let app, browser;
try {
  app = await startService(join(dir, "data"), 0, {
    discover: async (harness) => ({
      harness,
      state: "unavailable",
      message: "Synthetic artwork fixture",
      sessions: [],
    }),
  });
  const client = new RelayClient(app.connection, app.token);
  const project = app.store.addProject({
    root: projectRoot,
    name: "Payments API",
  });
  const examples = [
    ["claude", "Payment retries & idempotency"],
    ["codex", "Billing API regression tests"],
    ["opencode", "Worker restart handling"],
    ["goose", "Observability follow-up"],
  ];
  const sessions = examples.map(
    ([harness, title], index) =>
      app.store.importSession({
        projectId: project.id,
        harness,
        title,
        externalId: `artwork-${index}`,
        events: [
          {
            id: "request",
            role: "user",
            text: "Prevent duplicate charges across retries. Keep idempotency keys for seven days.",
          },
          {
            id: "decision",
            role: "assistant",
            text: "Use a durable claim per provider event and verify concurrent delivery.",
          },
          {
            id: "test",
            role: "tool",
            text: "Synthetic test result: worker restart case needs a regression test.",
          },
        ],
      }).session,
  );
  for (const [index, session] of sessions.entries())
    await client.request("/api/graph/position", {
      sessionId: session.id,
      x: (index % 2) * 365,
      y: Math.floor(index / 2) * 260 + (index % 2 ? 45 : 0),
    });

  browser = await chromium.launch(
    process.env.RELAY_CHROME
      ? { executablePath: process.env.RELAY_CHROME }
      : {},
  );
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  await page.goto(`${app.connection.url}/#token=${app.token}`);
  await page.locator("[data-node]").first().waitFor();
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(output, "thread-map.png") });

  await page.locator(`[data-port="${sessions[0].id}"]`).click();
  await page.locator(`[data-node="${sessions[1].id}"]`).click();
  await page.getByRole("heading", { name: "Review context" }).waitFor();
  await page
    .locator("#goal")
    .fill("Prevent duplicate charges after retries and worker restarts.");
  await page.mouse.move(650, 770);
  await page.mouse.down();
  await page.mouse.move(470, 770, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(2600);
  await page.screenshot({ path: join(output, "thread-review.png") });
  await page.getByRole("button", { name: "Connect & send" }).click();
  await page.getByRole("heading", { name: "Connection" }).waitFor();
  await page.locator(".wire").first().waitFor();
  await page.waitForTimeout(2600);
  await page.screenshot({ path: join(output, "thread-connected.png") });
  console.log(
    "Captured map, review and connected states from synthetic sessions.",
  );
} finally {
  await browser?.close();
  await app?.close();
  rmSync(dir, { recursive: true, force: true });
}
