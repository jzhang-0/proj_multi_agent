import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8"));
const packages = [];

for (const [location, metadata] of Object.entries(lock.packages ?? {})) {
  if (!location || metadata.dev === true) continue;
  const packageDir = join(root, location);
  const packageJson = join(packageDir, "package.json");
  if (!existsSync(packageJson)) continue;
  const info = JSON.parse(readFileSync(packageJson, "utf8"));
  const licenseFile = readdirSync(packageDir).find((entry) => /^((licen[cs]e|copying)(\.|$))/i.test(entry));
  packages.push({
    name: info.name,
    version: info.version,
    license: typeof info.license === "string" ? info.license : info.license?.type ?? "UNKNOWN",
    author: typeof info.author === "string" ? info.author : info.author?.name ?? "",
    license_file: licenseFile,
    license_text: licenseFile ? readFileSync(join(packageDir, licenseFile), "utf8") : null,
  });
}

packages.sort((left, right) => `${left.name}@${left.version}`.localeCompare(`${right.name}@${right.version}`));
const output = `${JSON.stringify({ generated_by: "web/scripts/licenses.mjs", packages }, null, 2)}\n`;
writeFileSync(join(root, "THIRD_PARTY_LICENSES.json"), output, "utf8");
console.log(`wrote ${packages.length} production dependency licenses`);
