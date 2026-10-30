import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const root = fileURLToPath(new URL("../../", import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "relay-package-"));
const npm = process.env.npm_execpath;
if (!npm) throw new Error("Run this gate with npm run test:package.");
// Exercise an anonymous consumer install without borrowing the maintainer's
// registry credentials, global tools or npm cache.
const npmEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(
    ([key]) =>
      !/^npm_config_/i.test(key) &&
      !["NPM_TOKEN", "NODE_AUTH_TOKEN"].includes(key),
  ),
);
writeFileSync(
  join(temporary, "user.npmrc"),
  "registry=https://registry.npmjs.org/\n",
  { mode: 0o600 },
);
writeFileSync(join(temporary, "global.npmrc"), "");
Object.assign(npmEnvironment, {
  npm_config_userconfig: join(temporary, "user.npmrc"),
  npm_config_globalconfig: join(temporary, "global.npmrc"),
  npm_config_cache: join(temporary, "cache"),
  npm_config_registry: "https://registry.npmjs.org/",
});
const runNpm = (args, cwd = root) =>
  execFileSync(process.execPath, [npm, ...args], {
    cwd,
    env: npmEnvironment,
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
      /(?:workspace\.db|access-token|\.env$|test\/|\.whl$|\.py$)/.test(
        file.path,
      ),
    ),
  );
  runNpm([
    "install",
    "--global",
    "--prefix",
    join(temporary, "install"),
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    join(temporary, packed.filename),
  ]);
  cli = join(
    temporary,
    "install",
    ...(process.platform === "win32" ? [] : ["lib"]),
    "node_modules/@mahekara28/context-relay/dist/cli.js",
  );
  const expected = JSON.parse(
    readFileSync(join(root, "package.json"), "utf8"),
  ).version;
  const commandCheck = join(temporary, "command-check");
  mkdirSync(commandCheck);
  writeFileSync(
    join(commandCheck, "package.json"),
    JSON.stringify({ private: true, scripts: { check: "relay --version" } }),
  );
  const pathKey =
    Object.keys(npmEnvironment).find((key) => key.toLowerCase() === "path") ||
    "PATH";
  const originalPath = npmEnvironment[pathKey];
  const globalBin = join(
    temporary,
    "install",
    ...(process.platform === "win32" ? [] : ["bin"]),
  );
  npmEnvironment[pathKey] = globalBin + delimiter + originalPath;
  const globalVersion = runNpm(["run", "--silent", "check"], commandCheck);
  npmEnvironment[pathKey] = originalPath;
  assert.equal(globalVersion.trim(), expected);
  const ephemeral = [
    "exec",
    "--yes",
    "--package",
    join(temporary, packed.filename),
    "--",
    "relay",
  ];
  assert.equal(runNpm([...ephemeral, "--version"], temporary).trim(), expected);
  assert.match(runNpm([...ephemeral, "--help"], temporary), /Usage: relay/);
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
    `Installed tarball ${packed.filename}: global command, npm exec/npx, service, checkpoint, acceptance and restart passed.`,
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
