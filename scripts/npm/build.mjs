import { chmodSync, cpSync, rmSync } from "node:fs";
chmodSync(new URL("../../dist/cli.js", import.meta.url), 0o755);
rmSync(new URL("../../dist/ui", import.meta.url), {
  recursive: true,
  force: true,
});
cpSync(
  new URL("../../packages/relay/ui", import.meta.url),
  new URL("../../dist/ui", import.meta.url),
  { recursive: true },
);
