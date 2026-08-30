import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../dist/", import.meta.url)));
const port = Number(process.env.PORT ?? 4173);
const contentTypes = { ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8" };

const server = createServer((request, response) => {
  const requestPath = new URL(request.url ?? "/", "http://127.0.0.1").pathname;
  const relative = requestPath === "/" ? "index.html" : requestPath.slice(1);
  let file = resolve(normalize(join(root, relative)));
  if (!file.startsWith(`${root}/`)) {
    response.writeHead(403).end("forbidden");
    return;
  }
  try {
    if (!statSync(file).isFile()) throw new Error("not a file");
    response.writeHead(200, { "Content-Type": contentTypes[extname(file)] ?? "application/octet-stream" });
    createReadStream(file).pipe(response);
  } catch {
    if (["/timeline", "/workspace", "/help"].includes(requestPath) || requestPath.startsWith("/task/")) {
      file = resolve(root, "index.html");
      response.writeHead(200, { "Content-Type": contentTypes[".html"] });
      createReadStream(file).pipe(response);
      return;
    }
    response.writeHead(404).end("not found");
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`serving ${root} at http://127.0.0.1:${port}`);
});
