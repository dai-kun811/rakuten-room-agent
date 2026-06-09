import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import url from "node:url";

const port = 8787;
const root = path.dirname(url.fileURLToPath(import.meta.url));

const contentTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".htm", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".svg", "image/svg+xml"],
  [".ico", "image/x-icon"]
]);

function localUrls() {
  const urls = [];
  for (const entries of Object.values(os.networkInterfaces())) {
    for (const entry of entries || []) {
      if (entry.family === "IPv4" && !entry.internal) {
        urls.push(`http://${entry.address}:${port}/`);
      }
    }
  }
  return [...new Set(urls)];
}

const server = http.createServer((request, response) => {
  const requestUrl = new URL(request.url || "/", `http://127.0.0.1:${port}`);
  const pathname = decodeURIComponent(requestUrl.pathname);
  const relativePath = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const filePath = path.resolve(root, relativePath);

  if (!filePath.startsWith(root) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    response.writeHead(404, {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store"
    });
    response.end("Not found");
    return;
  }

  const contentType = contentTypes.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
  response.writeHead(200, {
    "Content-Type": contentType,
    "Cache-Control": "no-store"
  });
  fs.createReadStream(filePath).pipe(response);
});

server.listen(port, "0.0.0.0", () => {
  console.log("");
  console.log("Rakuten ROOM routine server is running.");
  console.log("Connect your phone to the same Wi-Fi and open:");
  console.log("");
  console.log(`  http://127.0.0.1:${port}/`);
  for (const localUrl of localUrls()) {
    console.log(`  ${localUrl}`);
  }
  console.log("");
  console.log("Press Ctrl + C to stop.");
});
