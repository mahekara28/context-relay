import { chromium } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = new URL("../../", import.meta.url);
const { publishedVersion } = JSON.parse(
  await readFile(new URL("site/release.json", root), "utf8"),
);
const logo = Buffer.from(
  await readFile(new URL("packages/relay/ui/assets/relay.svg", root)),
).toString("base64");
const map = Buffer.from(
  await readFile(new URL("docs/assets/workspace/thread-connected.png", root)),
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
    body{margin:0;padding:48px;background:#f6f5f1;color:#222827;font-family:Arial,sans-serif}
    header{display:flex;align-items:center;gap:13px;font-size:23px;font-weight:700;letter-spacing:-.7px}
    header img{width:38px;height:38px}
    .preview{margin-left:auto;font:14px monospace;color:#626a65;letter-spacing:0}
    main{display:grid;grid-template-columns:465px 1fr;gap:35px;align-items:center;margin-top:65px}
    h1{font-size:65px;line-height:1.07;letter-spacing:-3.5px;margin:0 0 21px}
    h1 span{color:#bc3c23}
    p{font-size:21px;line-height:1.5;color:#626a65;margin:0}
    .capture{margin:0;border:1px solid #dcded7;border-radius:9px;overflow:hidden;box-shadow:0 16px 38px #252d291a;background:white}
    .capture img{display:block;width:100%}
    .capture figcaption{font:12px monospace;color:#626a65;padding:10px 13px;border-top:1px solid #dcded7}
    footer{position:absolute;left:48px;right:48px;bottom:22px;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #dcded7;padding-top:13px;font:13px monospace;color:#626a65}
    footer strong{color:#bc3c23;font-weight:500}
  </style><header><img src="data:image/svg+xml;base64,${logo}" alt="">Context Relay<span class="preview">LOCAL THREAD MAP</span></header><main><div><h1>Keep the work.<br><span>Change the thread.</span></h1><p>Discover local agent sessions. Connect them visually. Review every handoff.</p></div><figure class="capture"><img src="data:image/png;base64,${map}" alt="Synthetic Context Relay thread map"><figcaption>Actual UI · synthetic sessions</figcaption></figure></main><footer><span>mahekara28.github.io/context-relay</span><strong>npm beta ${publishedVersion} · map preview from source</strong></footer></html>`);
  await page.screenshot({
    path: fileURLToPath(new URL("site/social-card.png", root)),
  });
} finally {
  await browser.close();
}
