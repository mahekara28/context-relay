import { chromium } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../../", import.meta.url);
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
    *{box-sizing:border-box}body{margin:0;padding:56px 64px;background:#f6f5f1;color:#222827;font-family:Arial,sans-serif}.brand{display:flex;align-items:center;gap:14px;font-size:25px;font-weight:600;letter-spacing:-1px}.brand img{width:44px;height:44px}.badge{margin-left:auto;font:13px monospace;color:#626a65;border:1px solid #dcded7;padding:10px 14px;border-radius:4px}h1{font-size:82px;line-height:1.08;letter-spacing:-5px;margin:62px 0 25px;font-weight:600}h1 span{color:#bc3c23}p{font-size:22px;line-height:1.5;color:#626a65;max-width:800px}footer{display:flex;align-items:center;justify-content:space-between;border-top:1px solid #dcded7;padding-top:23px;margin-top:43px;font:15px monospace;color:#626a65}footer strong{color:#222827;font-weight:400}
  </style><div class="brand"><img src="data:image/svg+xml;base64,${logo}" alt="">Context Relay<span class="badge">LOCAL CLI + WORKSPACE + MCP</span></div><h1>Keep the work.<br><span>Change the thread.</span></h1><p>Carry reviewed decisions, evidence and open work<br>into your next coding session.</p><footer><strong>Claude Code · Codex · OpenCode · Goose</strong><span>mahekara28.github.io/context-relay</span></footer></html>`);
  await page.screenshot({
    path: fileURLToPath(new URL("site/social-card.png", root)),
  });
} finally {
  await browser.close();
}
