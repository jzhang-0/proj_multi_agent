import { build } from "esbuild";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(new URL("..", import.meta.url).pathname);
const dist = resolve(root, "web/dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(resolve(dist, "assets"), { recursive: true });

await build({
  entryPoints: [resolve(root, "web/src/main.tsx")],
  bundle: true,
  format: "esm",
  minify: true,
  outfile: resolve(dist, "assets/app.js"),
  target: "es2022",
});

cpSync(resolve(root, "web/index.html"), resolve(dist, "index.html"));
cpSync(
  resolve(root, "node_modules/@xterm/xterm/css/xterm.css"),
  resolve(dist, "assets/xterm.css"),
);
console.log(`built ${resolve(dist, "assets/app.js")}`);
