import { test, expect } from "@playwright/test";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startService } from "../../../dist/server.js";
let app, directory, project, session, first, second;
test.beforeAll(async () => {
  directory = mkdtempSync(join(tmpdir(), "relay-browser-"));
  const root = join(directory, "project");
  mkdirSync(root);
  app = await startService(join(directory, "data"));
  project = app.store.addProject({ root, name: "Webhook recovery" });
  session = app.store.importSession({
    projectId: project.id,
    harness: "claude",
    externalId: "synthetic",
    title: "Duplicate webhook investigation",
    events: [
      { id: "u1", role: "user", text: "Retain idempotency keys for 24 hours." },
      {
        id: "u2",
        role: "user",
        text: "Correction: retain idempotency keys for 7 days.",
      },
      {
        id: "t1",
        role: "tool",
        kind: "tool_result",
        text: 'Synthetic fixture: duplicate delivery test is failing. <script>throw new Error("untrusted")</script>',
      },
    ],
  }).session;
  const draft = {
    projectId: project.id,
    title: "Original retention decision",
    goal: "Prevent duplicate webhook charges.",
    sources: [{ sessionId: session.id, revision: 1 }],
    instructions: [
      {
        key: "retention",
        text: "24 hours",
        sources: [{ sessionId: session.id, eventId: "u1" }],
      },
    ],
    evidence: [{ sessionId: session.id, eventId: "u1" }],
  };
  first = app.store.createCheckpoint(draft);
  second = app.store.createCheckpoint({
    ...draft,
    title: "Corrected retention decision",
    instructions: [
      {
        key: "retention",
        text: "7 days",
        sources: [{ sessionId: session.id, eventId: "u2" }],
      },
    ],
    evidence: [{ sessionId: session.id, eventId: "u2" }],
  });
});
test.afterAll(async () => {
  await app.close();
  rmSync(directory, { recursive: true, force: true });
});
async function enter(page) {
  await page.goto(app.connection.url + "/#token=" + app.token);
  await expect(
    page.getByRole("heading", { name: "Pick up where you left off." }),
  ).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");
}
test("source review creates a real handoff, exports it, and never executes source text", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await enter(page);
  await page.locator('[data-evidence="u2"]').check();
  await page.locator('[data-evidence="t1"]').check();
  await page.getByRole("button", { name: "Review handoff →" }).click();
  await page
    .locator('[data-draft="goal"]')
    .fill("Prevent duplicate webhook charges.");
  await page.getByRole("button", { name: "+ Add instruction" }).click();
  await page.locator('[data-field="key"]').fill("retention");
  await page
    .locator('[data-field="text"]')
    .fill("Retain idempotency keys for 7 days.");
  await page
    .locator('[data-draft="criteria"]')
    .fill("Duplicate deliveries cause one charge.");
  await page.locator("#review-confirm").check();
  await page
    .getByRole("button", { name: "Save checkpoint & offer handoff" })
    .click();
  await expect(
    page.getByRole("heading", { name: "A clean start, with the context." }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export Markdown" }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/relay-handoff/);
  expect(app.store.handoffs().length).toBe(1);
  expect(app.store.handoffs()[0].packet.instructions[0].text).toContain(
    "7 days",
  );
  expect(errors).toEqual([]);
});
test("merge requires an explicit conflict choice and preserves parents", async ({
  page,
}) => {
  await enter(page);
  await page.getByRole("button", { name: "Checkpoints", exact: true }).click();
  await page.locator(`[data-merge="${first.id}"]`).check();
  await page.locator(`[data-merge="${second.id}"]`).check();
  await page.getByRole("button", { name: "Merge selected (2)" }).click();
  await expect(page.getByText("Resolve: retention")).toBeVisible();
  await page.getByLabel("7 days", { exact: true }).check();
  await page.locator("#review-confirm").check();
  await page
    .getByRole("button", { name: "Save checkpoint & offer handoff" })
    .click();
  await expect(
    page.getByRole("heading", { name: "A clean start, with the context." }),
  ).toBeVisible();
  const merged = app.store
    .checkpoints(project.id)
    .find((c) => c.parents.length === 2);
  expect(merged.resolutions.retention).toBe("7 days");
  expect(app.store.checkpoint(first.id).instructions[0].text).toBe("24 hours");
});
test("mobile and dark views stay within the viewport and remain navigable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await enter(page);
  for (const view of [
    "Threads",
    "Checkpoints",
    "Handoffs",
    "Connections",
    "Architecture",
  ]) {
    await page.getByRole("button", { name: view, exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.getByRole("button", { name: "Add connection" }).count();
});
test("unauthenticated workspace shows the local login and no project data", async ({
  page,
}) => {
  await page.goto(app.connection.url);
  await expect(
    page.getByRole("heading", { name: "Your context stays with you." }),
  ).toBeVisible();
  await expect(page.getByText("Webhook recovery")).toHaveCount(0);
});
