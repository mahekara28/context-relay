import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const root = fileURLToPath(new URL("../../", import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "relay-package-"));
const npm = process.env.npm_execpath;
if (!npm) throw new Error("Run this gate with npm run test:package.");
const runNpm = (args) =>
  execFileSync(process.execPath, [npm, ...args], {
    cwd: root,
    encoding: "utf8",
    timeout: 120000,
    maxBuffer: 4 * 1024 * 1024,
  });
let cli;
const data = join(temporary, "profile");
const run = (args) =>
  execFileSync(process.execPath, [cli, ...args, "--data-dir", data], {
    cwd: temporary,
    encoding: "utf8",
    timeout: 30000,
    stdio: ["ignore", "pipe", "pipe"],
  });
try {
  const packed = JSON.parse(
    runNpm([
      "pack",
      "--json",
      "--ignore-scripts",
      "--pack-destination",
      temporary,
    ]),
  )[0];
  assert(packed.files.some((file) => file.path === "dist/cli.js"));
  assert(
    !packed.files.some((file) =>
      /(?:workspace\.db|access-token|\.env$|test\/)/.test(file.path),
    ),
  );
  runNpm([
    "install",
    "--prefix",
    join(temporary, "install"),
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    join(temporary, packed.filename),
  ]);
  cli = join(
    temporary,
    "install/node_modules/@mahekara28/context-relay/dist/cli.js",
  );
  const expected = JSON.parse(
    readFileSync(join(root, "package.json"), "utf8"),
  ).version;
  assert.equal(run(["--version"]).trim(), expected);
  assert.match(run(["--help"]), /Context Relay/);
  assert(packed.files.some((file) => file.path === "dist/ui/app.js"));
  const uiUrl = new URL(run(["ui", "--no-open"]).trim());
  assert(uiUrl.hash.startsWith("#token="));
  const page = await fetch(uiUrl.origin);
  assert.match(
    page.headers.get("content-security-policy"),
    /frame-ancestors 'none'/,
  );
  assert.match(await page.text(), /Context Relay/);
  assert.equal((await fetch(uiUrl.origin + "/assets/goose.svg")).status, 200);
  assert.equal((await fetch(uiUrl.origin + "/api/projects")).status, 401);
  const demo = JSON.parse(run(["demo"]));
  assert.equal(demo.synthetic, true);
  assert.equal(demo.status, "offered");
  const packet = JSON.parse(
    run(["checkpoint", "packet", "--id", demo.checkpointId]),
  );
  assert.equal(
    packet.instructions[0].text,
    "Retain idempotency keys for 7 days.",
  );
  const accepted = JSON.parse(
    run(["handoff", "accept", "--id", demo.handoffId, "--recipient", "codex"]),
  );
  assert.equal(accepted.status, "accepted");
  assert.equal(JSON.parse(run(["status"])).name, "context-relay");
  run(["stop"]);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const restart = JSON.parse(run(["init"]));
  assert(restart.url.startsWith("http://127.0.0.1:"));
  assert.equal(
    JSON.parse(run(["handoff", "show", "--id", demo.handoffId])).status,
    "accepted",
  );
  console.log(
    `Installed tarball ${packed.filename}: CLI, service, checkpoint, acceptance and restart passed.`,
  );
} finally {
  if (cli) {
    try {
      run(["stop"]);
      await new Promise((resolve) => setTimeout(resolve, 500));
    } catch {}
  }
  rmSync(temporary, { recursive: true, force: true });
}
