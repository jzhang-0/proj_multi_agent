import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve(new URL("../web/dist", import.meta.url).pathname);
const types = { ".css": "text/css", ".html": "text/html", ".js": "text/javascript" };
const server = createServer((request, response) => {
  const requested = decodeURIComponent((request.url || "/").split("?")[0]);
  const relative = requested === "/" ? "/index.html" : requested;
  const file = normalize(join(root, relative));
  if (!file.startsWith(root) || !statExists(file)) {
    response.writeHead(404);
    response.end("not found");
    return;
  }
  response.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream" });
  createReadStream(file).pipe(response);
});

function statExists(file) {
  try {
    return statSync(file).isFile();
  } catch {
    return false;
  }
}

const port = Number(process.env.PORT || 4173);
server.listen(port, "127.0.0.1", () => console.log(`http://127.0.0.1:${port}`));
