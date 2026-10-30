import { test, expect } from "@playwright/test";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
  appendFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startService } from "../../../dist/server.js";
import { discoverLocal } from "../../../dist/adapters/local.js";
import { RelayClient } from "../../../dist/client.js";
let app, dir, client, project, threads;
test.beforeEach(async () => {
  dir = mkdtempSync(join(tmpdir(), "relay-map-browser-"));
  const root = join(dir, "context-relay");
  mkdirSync(root);
  mkdirSync(join(dir, "claude"));
  writeFileSync(
    join(dir, "claude/new-thread.jsonl"),
    JSON.stringify({
      type: "user",
      uuid: "live-1",
      sessionId: "new-thread",
      cwd: root,
      message: { content: "Discovered by the local collector." },
    }) + "\n",
  );
  app = await startService(join(dir, "data"), 0, {
    discover: async (harness) =>
      harness === "claude"
        ? discoverLocal("claude", { source: join(dir, "claude") })
        : {
            harness,
            state: "unavailable",
            message: "Isolated browser fixture",
            sessions: [],
          },
  });
  client = new RelayClient(app.connection, app.token);
  await client.request("/api/workspace", { enabled: false });
  project = app.store.addProject({ root, name: "Context Relay" });
  threads = [
    ["claude", "Designing the session graph"],
    ["codex", "Building the local indexer"],
    ["goose", "Testing context handoffs"],
    ["claude", "Reviewing source freshness"],
    ["codex", "Polishing the thread canvas"],
    ["opencode", "Checking MCP integration"],
  ].map(
    ([harness, title], i) =>
      app.store.importSession({
        projectId: project.id,
        harness,
        title,
        externalId: "fixture-" + i,
        events: [
          {
            id: "u1",
            role: "user",
            text: "Keep the project local. Carry decisions and open work into the next thread.",
          },
          {
            id: "a1",
            role: "assistant",
            text: "The collector reads completed turns and records the source revision.",
          },
          {
            id: "t1",
            role: "tool",
            text: 'Fixture output: <script>throw new Error("untrusted")</script>',
          },
        ],
      }).session,
  );
});
test.afterEach(async () => {
  await app.close();
  rmSync(dir, { recursive: true, force: true });
});
async function enter(page) {
  await page.goto(app.connection.url + "/#token=" + app.token);
  await expect(page.getByRole("heading", { name: "Thread map" })).toBeVisible();
  await expect(page.locator("[data-node]")).toHaveCount(6);
  expect(new URL(page.url()).hash).toBe("");
}
test("graph discovers real indexed threads, filters, preserves positions and pans/zooms", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await enter(page);
  const node = page.locator(`[data-node="${threads[0].id}"]`);
  const before = await node.boundingBox();
  await page.mouse.move(before.x + 100, before.y + 90);
  await page.mouse.down();
  await page.mouse.move(before.x + 155, before.y + 125, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        Object.keys((await client.request("/api/graph")).positions).length,
    )
    .toBe(1);
  await page.reload();
  await expect(node).toBeVisible();
  expect(
    (await client.request("/api/graph")).positions[threads[0].id].x,
  ).toBeGreaterThan(0);
  await page.getByRole("searchbox", { name: "Find a thread" }).fill("indexer");
  await expect(page.locator("[data-node]")).toHaveCount(1);
  await page.getByRole("searchbox", { name: "Find a thread" }).fill("");
  await expect(page.locator("[data-node]")).toHaveCount(6);
  await page.getByRole("button", { name: "Zoom out", exact: true }).click();
  await page.getByRole("button", { name: "Fit threads to view" }).click();
  await page.screenshot({
    path: "/tmp/context-relay-thread-map.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("dragging source to destination creates a reviewed targeted handoff and reflects MCP acceptance", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await enter(page);
  const from = await page
      .locator(`[data-port="${threads[0].id}"]`)
      .boundingBox(),
    to = await page.locator(`[data-node="${threads[1].id}"]`).boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {
    steps: 12,
  });
  await page.mouse.up();
  await expect(
    page.getByRole("heading", { name: "Review context", exact: true }),
  ).toBeVisible();
  await expect(page.locator("#target")).toHaveValue(threads[1].id);
  await page
    .locator("#goal")
    .fill("Implement the session graph with a local collector.");
  await page
    .getByRole("button", { name: "Connect & send", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Connection", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".drawer .pill").first()).toHaveText("queued");
  const handoff = app.store.handoffs()[0];
  expect(handoff.packet.destination.sessionId).toBe(threads[1].id);
  expect(handoff.packet.evidence.length).toBe(3);
  await client.request(`/api/handoffs/${handoff.id}/accept`, {
    recipient: "codex",
    destinationExternalId: threads[1].externalId,
  });
  await expect(page.locator(".drawer .pill").first()).toHaveText("accepted", {
    timeout: 7000,
  });
  await expect(page.locator(".wire.accepted")).toHaveCount(1);
  await page.screenshot({
    path: "/tmp/context-relay-connected-map.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
test("keyboard selection combines multiple source threads; cancellation and stale state stay visible", async ({
  page,
}) => {
  await enter(page);
  for (const s of [threads[0], threads[2]])
    await page.locator(`[data-pick="${s.id}"]`).click();
  await page
    .getByRole("button", { name: "Relay context", exact: true })
    .click();
  await page.locator("#target").selectOption(threads[1].id);
  await page
    .getByRole("button", { name: "Connect & send", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Connection", exact: true }),
  ).toBeVisible();
  expect(app.store.handoffs()[0].packet.sources.length).toBe(2);
  await expect(page.locator(".wire")).toHaveCount(2);
  app.store.importSession({
    projectId: project.id,
    harness: threads[0].harness,
    title: threads[0].title,
    externalId: threads[0].externalId,
    events: [
      {
        id: "u2",
        role: "user",
        text: "Update: use a longer retention window.",
      },
    ],
  });
  await expect(page.locator(".drawer .pill").first()).toHaveText("stale", {
    timeout: 7000,
  });
  await page
    .getByRole("button", { name: "Cancel handoff", exact: true })
    .click();
  await expect(page.locator(".drawer .pill").first()).toHaveText("cancelled");
});
test("mobile, dark mode, empty search and sources remain usable without page overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await enter(page);
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Sources & settings" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Close panel" }).click();
  await page
    .getByRole("searchbox", { name: "Find a thread" })
    .fill("no-such-thread");
  await expect(
    page.getByRole("heading", { name: "No matching threads" }),
  ).toBeVisible();
  await page.screenshot({
    path: "/tmp/context-relay-thread-map-mobile.png",
    fullPage: true,
  });
});
test("unauthenticated map reveals no session metadata", async ({ page }) => {
  await page.goto(app.connection.url);
  await expect(
    page.getByRole("heading", { name: "Your threads, connected." }),
  ).toBeVisible();
  await expect(page.getByText(threads[0].title)).toHaveCount(0);
});

test("first opening starts discovery; new native messages arrive without manual import", async ({
  page,
}) => {
  app.store.db
    .prepare("DELETE FROM workspace_state WHERE key='settings'")
    .run();
  await page.goto(app.connection.url + "/#token=" + app.token);
  await expect(page.locator("[data-node]")).toHaveCount(7, { timeout: 10000 });
  const captured = app.store
    .sessions()
    .find((s) => s.externalId === "new-thread");
  expect(captured.eventCount).toBe(1);
  appendFileSync(
    join(dir, "claude/new-thread.jsonl"),
    JSON.stringify({
      type: "user",
      uuid: "live-2",
      sessionId: "new-thread",
      cwd: project.root,
      message: { content: "The next message arrived." },
    }) + "\n",
  );
  await client.request("/api/workspace/scan", {});
  await expect(page.locator(`[data-node="${captured.id}"] code`)).toHaveText(
    "2 events",
    { timeout: 10000 },
  );
  const boxes = await page
    .locator("[data-node]")
    .evaluateAll((nodes) =>
      nodes.map((n) => ({ x: n.style.transform, y: n.dataset.node })),
    );
  expect(new Set(boxes.map((b) => b.x)).size).toBe(7);
});
test("a pending graph connection can be rewired to another destination", async ({
  page,
}) => {
  await enter(page);
  await page.locator(`[data-pick="${threads[0].id}"]`).click();
  await page
    .getByRole("button", { name: "Relay context", exact: true })
    .click();
  await page.locator("#target").selectOption(threads[1].id);
  await page
    .getByRole("button", { name: "Connect & send", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Change destination", exact: true })
    .click();
  await page.locator("#target").selectOption(threads[2].id);
  await page
    .getByRole("button", { name: "Update & send", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Connection", exact: true }),
  ).toBeVisible();
  expect(
    app.store.handoffs().filter((h) => h.status === "offered")[0].packet
      .destination.sessionId,
  ).toBe(threads[2].id);
  expect(
    app.store.handoffs().filter((h) => h.status === "cancelled"),
  ).toHaveLength(1);
});

test("connectors snap near a destination, keep the viewport still, and retain a draft wire", async ({
  page,
}) => {
  await enter(page);
  const before = await page.locator("#world").getAttribute("style");
  const from = await page
    .locator(`[data-port="${threads[0].id}"]`)
    .boundingBox();
  const to = await page.locator(`[data-node="${threads[1].id}"]`).boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x - 12, to.y + to.height / 2, { steps: 20 });
  await expect(page.locator(`[data-node="${threads[1].id}"]`)).toHaveClass(
    /target-hover/,
  );
  await page.mouse.up();
  await expect(page.locator("#target")).toHaveValue(threads[1].id);
  expect(await page.locator("#world").getAttribute("style")).toBe(before);
  await expect(page.locator(".wire.preview")).toHaveCount(1);
  await page.getByRole("button", { name: "Close panel" }).click();
  await expect(page.locator(".wire.preview")).toHaveCount(0);
});

test("click and keyboard connect work without dragging, and Escape cancels cleanly", async ({
  page,
}) => {
  await enter(page);
  const port = page.locator(`[data-port="${threads[0].id}"]`),
    target = page.locator(`[data-node="${threads[1].id}"]`);
  await port.focus();
  await page.keyboard.press("Enter");
  await expect(target).toHaveClass(/target-eligible/);
  await page.keyboard.press("Escape");
  await expect(target).not.toHaveClass(/target-eligible/);
  await port.focus();
  await page.keyboard.press("Enter");
  await target.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#target")).toHaveValue(threads[1].id);
  await expect(
    page.getByRole("button", { name: "Connect & send", exact: true }),
  ).toBeVisible();
});

test("background updates do not replace a card under a pointer or reset its movement", async ({
  page,
}) => {
  await enter(page);
  const selector = `[data-node="${threads[0].id}"]`;
  await page.evaluate(
    (selector) => (window.draggedCard = document.querySelector(selector)),
    selector,
  );
  const from = await page.locator(selector).boundingBox();
  await page.mouse.move(from.x + 80, from.y + 70);
  await page.mouse.down();
  await page.mouse.move(from.x + 160, from.y + 90, { steps: 10 });
  app.store.importSession({
    projectId: project.id,
    harness: threads[0].harness,
    externalId: threads[0].externalId,
    title: threads[0].title,
    events: [{ id: "fresh", role: "user", text: "New data while dragging." }],
  });
  await page.waitForTimeout(3300);
  expect(
    await page.evaluate(
      (selector) => window.draggedCard === document.querySelector(selector),
      selector,
    ),
  ).toBe(true);
  await page.mouse.move(from.x + 200, from.y + 110, { steps: 10 });
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (await client.request("/api/graph")).positions[threads[0].id]?.x,
    )
    .toBeGreaterThan(60);
  await expect(page.locator(selector + " code")).toHaveText("4 events", {
    timeout: 7000,
  });
  expect(
    await page.evaluate(
      (selector) => window.draggedCard === document.querySelector(selector),
      selector,
    ),
  ).toBe(true);
});

test("Connect & send reaches a bound receiver without showing a copy-prompt step", async ({
  page,
}) => {
  const receiver = await client.request("/api/receivers", {
    projectId: project.id,
    harness: "claude",
    externalId: threads[3].externalId,
  });
  await enter(page);
  await page.locator(`[data-port="${threads[0].id}"]`).click();
  await page.locator(`[data-node="${threads[3].id}"]`).click();
  await page
    .getByRole("button", { name: "Connect & send", exact: true })
    .click();
  await expect(page.locator(".drawer .pill").first()).toHaveText("queued");
  await expect(
    page.getByRole("textbox", { name: "Continuation prompt" }),
  ).not.toBeVisible();
  const delivery = await client.request(
    `/api/receivers/${receiver.id}/next`,
    {},
  );
  expect(delivery.handoffId).toBeTruthy();
  await client.request(`/api/receivers/${receiver.id}/receipt`, {
    handoffId: delivery.handoffId,
    attemptId: delivery.attemptId,
    sent: true,
  });
  await expect(page.locator(".drawer .pill").first()).toHaveText("sent", {
    timeout: 7000,
  });
  await client.request(`/api/handoffs/${delivery.handoffId}/accept`, {
    recipient: "claude",
    destinationExternalId: threads[3].externalId,
  });
  await expect(page.locator(".drawer .pill").first()).toHaveText("accepted", {
    timeout: 7000,
  });
});

test("invalid targets stay muted and Escape rolls a drag back without creating a handoff", async ({
  page,
}) => {
  const otherRoot = join(dir, "other-project");
  mkdirSync(otherRoot);
  const otherProject = app.store.addProject({ root: otherRoot });
  const foreign = app.store.importSession({
    projectId: otherProject.id,
    harness: "claude",
    externalId: "foreign",
    title: "Another project",
    events: [],
  }).session;
  await page.goto(app.connection.url + "/#token=" + app.token);
  await expect(page.locator("[data-node]")).toHaveCount(7);
  const source = page.locator(`[data-node="${threads[0].id}"]`);
  const from = await page
    .locator(`[data-port="${threads[0].id}"]`)
    .boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 35, from.y + 55, { steps: 6 });
  await expect(page.locator(`[data-node="${foreign.id}"]`)).toHaveClass(
    /target-muted/,
  );
  await expect(source).toHaveClass(/target-muted/);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(page.locator(".wire.preview")).toHaveCount(0);
  await expect(page.locator(".drawer")).toHaveCount(0);
  expect(app.store.handoffs()).toHaveLength(0);
  const before = await source.boundingBox();
  await page.mouse.move(before.x + 70, before.y + 60);
  await page.mouse.down();
  await page.mouse.move(before.x + 140, before.y + 110, { steps: 8 });
  await page.keyboard.press("Escape");
  await page.mouse.up();
  const restored = await source.boundingBox();
  expect(Math.abs(restored.x - before.x)).toBeLessThan(1);
  expect(
    (await client.request("/api/graph")).positions[threads[0].id],
  ).toBeUndefined();
});
