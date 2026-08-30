import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(resolve(dist, "assets"), { recursive: true });
writeFileSync(resolve(dist, ".gitkeep"), "");

await build({
  entryPoints: [resolve(root, "src/main.tsx")],
  bundle: true,
  format: "esm",
  minify: true,
  outfile: resolve(dist, "assets/app.js"),
  target: "es2022",
});

cpSync(resolve(root, "index.html"), resolve(dist, "index.html"));
cpSync(resolve(root, "src/styles.css"), resolve(dist, "assets/app.css"));
// WEB-007:xterm.css 不经 JS import(esbuild 单文件 outfile 没配 CSS loader，
// 见 docs/web/terminal-protocol.md §10)，直接从包里复制一份，运行时由
// TerminalView 挂载时动态插入 <link>，非终端页面不用背这份 CSS 的加载成本。
cpSync(
  resolve(root, "node_modules/@xterm/xterm/css/xterm.css"),
  resolve(dist, "assets/xterm.css"),
);
const licenses = resolve(root, "THIRD_PARTY_LICENSES.json");
if (existsSync(licenses)) {
  cpSync(licenses, resolve(dist, "THIRD_PARTY_LICENSES.json"));
}
console.log(`built ${resolve(dist, "index.html")}`);
