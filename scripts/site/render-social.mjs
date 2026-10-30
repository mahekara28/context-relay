import { chromium } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../../", import.meta.url);
const { name, version } = JSON.parse(
  await readFile(new URL("package.json", root), "utf8"),
);
const logo = Buffer.from(
  await readFile(new URL("packages/relay/ui/assets/relay.svg", root)),
).toString("base64");
const browser = await chromium.launch({
  headless: true,
  ...(process.env.RELAY_CHROME
    ? { executablePath: process.env.RELAY_CHROME }
    : {}),
});
try {
  const page = await browser.newPage({
    viewport: { width: 1200, height: 630 },
    deviceScaleFactor: 1,
  });
  await page.setContent(`<!doctype html><html lang="en"><meta charset="utf-8"><style>
    *{box-sizing:border-box}
    body{margin:0;padding:48px 56px;background:#f6f5f1;color:#222827;font-family:Arial,sans-serif}
    .brand{display:flex;align-items:center;gap:14px;font-size:25px;font-weight:600;letter-spacing:-1px}
    .brand img{width:40px;height:40px}
    .badge{margin-left:auto;font:16px monospace;letter-spacing:0;color:#bc3c23;border:1px solid #dcded7;padding:12px 16px;border-radius:4px}
    h1{font-size:76px;line-height:1.08;letter-spacing:-4px;margin:40px 0 20px;font-weight:600}
    h1 span{color:#bc3c23}
    p{font-size:23px;line-height:1.5;color:#626a65;margin:0}
    .command{display:flex;align-items:center;justify-content:space-between;margin-top:27px;padding:21px 24px;background:#222827;color:#f3f4ed;border-radius:6px}
    .command code{font:25px monospace}.command span{font:16px monospace;color:#c3cbc3}
    footer{display:flex;align-items:center;justify-content:space-between;border-top:1px solid #dcded7;padding-top:23px;margin-top:30px;font:15px monospace;color:#626a65}
    footer strong{color:#222827;font-weight:400}
  </style><div class="brand"><img src="data:image/svg+xml;base64,${logo}" alt="">Context Relay<span class="badge">PUBLIC NPM BETA · ${version}</span></div><h1>Keep the work.<br><span>Change the thread.</span></h1><p>Reviewed context for your next coding session.<br>Local CLI · workspace · MCP</p><div class="command"><code>npx ${name}@beta ui</code><span>NODE 22.16+</span></div><footer><strong>Claude Code · Codex · OpenCode · Goose</strong><span>mahekara28.github.io/context-relay</span></footer></html>`);
  await page.screenshot({
    path: fileURLToPath(new URL("site/social-card.png", root)),
  });
} finally {
  await browser.close();
}
