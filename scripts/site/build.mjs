import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = path.join(root, "_site");
const { version } = JSON.parse(
  await readFile(path.join(root, "package.json"), "utf8"),
);
// A fixed allowlist keeps local workspace files, tests and source out of Pages.
const pages = [
  "index.html",
  "styles.css",
  "main.js",
  "404.html",
  "robots.txt",
  "sitemap.xml",
];
const logos = [
  "relay.svg",
  "claude.svg",
  "codex.svg",
  "opencode.svg",
  "goose.svg",
  "THIRD_PARTY_LICENSE.txt",
];
const captures = ["threads.png", "review.png", "handoff.png", "workflow.gif"];
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, "assets"), { recursive: true });
for (const name of pages) {
  const source = await readFile(path.join(root, "site", name), "utf8");
  await writeFile(
    path.join(output, name),
    source.replaceAll("{{VERSION}}", version),
  );
}
for (const name of logos)
  await copyFile(
    path.join(root, "packages/relay/ui/assets", name),
    path.join(output, "assets", name),
  );
for (const name of captures)
  await copyFile(
    path.join(root, "docs/assets/workspace", name),
    path.join(output, "assets", name),
  );
await copyFile(
  path.join(root, "site/social-card.png"),
  path.join(output, "assets/social-card.png"),
);
await writeFile(path.join(output, ".nojekyll"), "");
console.log(`Built GitHub Pages site for Context Relay ${version} in _site/`);
