import { test, expect } from "@playwright/test";
import { readFile, access } from "node:fs/promises";

test("project subpath loads every asset and links to real documentation", async ({
  page,
}) => {
  const failures = [];
  const origins = new Set();
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push(response.url());
  });
  page.on("request", (request) => origins.add(new URL(request.url()).origin));
  await page.goto("./");
  await expect(page).toHaveTitle(
    "Context Relay — keep the work, change the thread",
  );
  await page.locator("#tour-image").scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      page
        .locator("img")
        .evaluateAll((images) =>
          images.every((image) => image.complete && image.naturalWidth > 0),
        ),
    )
    .toBe(true);
  const links = await page
    .locator("a[href]")
    .evaluateAll((anchors) =>
      anchors.map((anchor) => anchor.getAttribute("href")),
    );
  for (const link of links) {
    if (link.startsWith("#") && link.length > 1)
      await expect(page.locator(link)).toHaveCount(1);
    const repoPath = link.split(
      "https://github.com/mahekara28/context-relay/blob/main/",
    )[1];
    if (repoPath) await access(repoPath.split("#")[0]);
  }
  expect(origins).toEqual(new Set(["http://127.0.0.1:4173"]));
  expect(failures).toEqual([]);
  await expect(page.locator("link[rel=canonical]")).toHaveAttribute(
    "href",
    "https://mahekara28.github.io/context-relay/",
  );
  const { publishedVersion } = JSON.parse(
    await readFile("site/release.json", "utf8"),
  );
  const { version: developmentVersion } = JSON.parse(
    await readFile("package.json", "utf8"),
  );
  await expect(page.locator("#install-command")).toContainText(
    "npm install --global @mahekara28/context-relay@beta",
  );
  await expect(page.locator("#install-command")).not.toContainText("git clone");
  await expect(page.locator(".install-status")).toContainText(
    `@${publishedVersion}`,
  );
  await expect(page.locator(".release-tag")).toContainText(publishedVersion);
  await expect(page.locator(".install-status")).not.toContainText(
    developmentVersion,
  );
  await expect(page.locator("#workspace")).toContainText(developmentVersion);
});

test("workspace tour supports keyboard navigation and full-size images", async ({
  page,
}) => {
  await page.goto("./");
  await page.getByRole("tab", { name: "01 Thread map" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "02 Review connection" }),
  ).toBeFocused();
  await expect(page.locator("#tour-panel")).toHaveAttribute(
    "aria-labelledby",
    "tab-review",
  );
  await expect(page.locator("#tour-image")).toHaveAttribute(
    "src",
    "./assets/thread-review.png",
  );
  await page.keyboard.press("End");
  await expect(
    page.getByRole("tab", { name: "03 Delivery status" }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#tour-fullscreen")).toHaveAttribute(
    "href",
    "./assets/thread-connected.png",
  );
  await expect
    .poll(() =>
      page
        .locator("#tour-image")
        .evaluate((image) => image.complete && image.naturalWidth > 0),
    )
    .toBe(true);
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "01 Thread map" })).toBeFocused();
});

test("copy commands preserve exact terminal text and switch harnesses", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("./");
  await page
    .getByRole("button", { name: "Copy install commands", exact: true })
    .click();
  const expected = await page.locator("#install-command").textContent();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    expected.trim(),
  );
  await page.getByRole("button", { name: "npx · try it now" }).click();
  await page
    .getByRole("button", { name: "Copy install commands", exact: true })
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "npx @mahekara28/context-relay@beta demo\nnpx @mahekara28/context-relay@beta ui",
  );
  await page.getByRole("button", { name: "npm · global install" }).click();
  await expect(page.locator("#install-command")).toHaveText(expected.trim());
  for (const harness of ["codex", "opencode", "goose", "claude"]) {
    await page.locator(`[data-harness=${harness}]`).click();
    await page
      .getByRole("button", { name: "Copy connection commands" })
      .click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(
      `--harness ${harness}\nrelay ui`,
    );
  }
  await page
    .getByRole("button", { name: "Copy npx command", exact: true })
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "npx @mahekara28/context-relay@beta ui",
  );
});

test("clipboard denial provides a selectable dialog and restores focus", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("Denied")) },
    }),
  );
  await page.goto("./");
  const copy = page.getByRole("button", {
    name: "Copy npx command",
    exact: true,
  });
  await copy.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Command to copy" }),
  ).toHaveValue("npx @mahekara28/context-relay@beta ui");
  await expect(
    page.getByRole("textbox", { name: "Command to copy" }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(copy).toBeFocused();
});

test("theme persists and the dashboard preview remains available", async ({
  page,
}) => {
  await page.goto("./");
  await expect(page.locator(".map-preview img")).toHaveAttribute(
    "src",
    "./assets/thread-connected.png",
  );
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByText("Is this a hosted workspace?", { exact: true }).click();
  await expect(page.locator("details[open]")).toContainText(
    "does not connect to your local service",
  );
});

for (const width of [320, 390, 768, 1024, 1440]) {
  test(`layout stays within ${width}px in both themes`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("./");
    for (const theme of ["light", "dark"]) {
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      for (const section of [
        "#main",
        "#workspace",
        "#architecture",
        "#connect",
        "#install",
        "#faq",
      ]) {
        await page.locator(section).scrollIntoViewIfNeeded();
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
      }
      if (theme === "light")
        await page
          .getByRole("button", { name: "Switch to dark theme" })
          .click();
    }
  });
}

test("404 remains styled and points back to the project root", async ({
  page,
}) => {
  const response = await page.goto("missing/nested/page");
  expect(response.status()).toBe(404);
  await expect(page).toHaveTitle("Page not found — Context Relay");
  await page.getByRole("link", { name: "Back to Context Relay" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:4173/context-relay/");
});
