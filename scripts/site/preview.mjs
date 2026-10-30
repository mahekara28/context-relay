import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../_site/", import.meta.url));
const base = "/context-relay/";
const types = {
  ".html": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".gif": "image/gif",
  ".txt": "text/plain",
  ".xml": "application/xml",
};
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  if (pathname === "/" || pathname === "/context-relay") {
    response.writeHead(302, { location: base });
    response.end();
    return;
  }
  let relative;
  try {
    relative = decodeURIComponent(pathname.slice(base.length));
  } catch {
    relative = "..";
  }
  const file = path.resolve(root, relative || "index.html");
  try {
    if (
      !pathname.startsWith(base) ||
      !file.startsWith(root) ||
      relative.includes("\0")
    )
      throw new Error("Not found");
    const body = await readFile(file);
    response.writeHead(200, {
      "content-type": `${types[path.extname(file)] || "application/octet-stream"}${/\.(html|css|js|txt|xml)$/.test(file) ? "; charset=utf-8" : ""}`,
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    });
    response.end(body);
  } catch {
    response.writeHead(404, { "content-type": "text/html; charset=utf-8" });
    response.end(await readFile(path.join(root, "404.html")));
  }
});
server.listen(Number(process.env.SITE_PORT || 4173), "127.0.0.1", () => {
  console.log(`Preview: http://127.0.0.1:${server.address().port}${base}`);
});
