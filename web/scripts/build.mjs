import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(resolve(dist, "assets"), { recursive: true });

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
const licenses = resolve(root, "THIRD_PARTY_LICENSES.json");
if (existsSync(licenses)) {
  cpSync(licenses, resolve(dist, "THIRD_PARTY_LICENSES.json"));
}
console.log(`built ${resolve(dist, "index.html")}`);
