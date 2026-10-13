import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, readdirSync, lstatSync, writeFileSync } from "node:fs";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const [mode, sourceArg, targetArg] = process.argv.slice(2);
try {
  if (!["--stage", "--verify"].includes(mode) || !sourceArg || (mode === "--stage" && !targetArg)) {
    throw new Error("Usage: node stage-android-release.mjs --stage RELEASE_DIR TARGET_DIR | --verify RELEASE_DIR");
  }
  const manifest = JSON.parse(readFileSync(join(root, "android-release.json"), "utf8"));
  const source = resolve(sourceArg);
  const entries = Object.entries(manifest.files);
  if (entries.length < 2 || entries.length % 2 !== 0 || !entries.some(([name]) => name.endsWith(".apk")) || !entries.some(([name]) => name.endsWith("-sources.tar.gz"))) throw new Error("Invalid release manifest");
  for (const [name] of entries) {
    const partner = name.endsWith(".apk") ? name.replace(/\.apk$/, "-sources.tar.gz") : name.replace(/-sources\.tar\.gz$/, ".apk");
    if (!manifest.files[partner]) throw new Error("Missing corresponding APK or sources");
  }
  const sums = entries.map(([name, hash]) => {
    if (!/^veilway-[a-z0-9.-]+\.(apk|tar\.gz)$/.test(name) || !/^[a-f0-9]{64}$/.test(hash)) throw new Error("Invalid artifact name or hash");
    const path = join(source, name);
    if (!lstatSync(path).isFile()) throw new Error("Artifact must be a regular file");
    const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
    if (actual !== hash) throw new Error(`Checksum mismatch: ${name}`);
    return `${hash}  ${name}\n`;
  }).join("");
  if (!lstatSync(join(source, "SHA256SUMS")).isFile() || readFileSync(join(source, "SHA256SUMS"), "utf8") !== sums) throw new Error("SHA256SUMS differs from pinned release");
  if (mode === "--verify") {
    const expected = [...entries.map(([name]) => name), "SHA256SUMS"].sort();
    if (JSON.stringify(readdirSync(source).sort()) !== JSON.stringify(expected)) throw new Error("Unexpected release files");
  } else {
    const target = resolve(targetArg);
    // Never overwrite a previously staged release; do not publish extra local files.
    mkdirSync(target, { recursive: false, mode: 0o755 });
    for (const [name] of entries) copyFileSync(join(source, name), join(target, name));
    writeFileSync(join(target, "SHA256SUMS"), sums, { mode: 0o644 });
  }
  console.log("Android release checksums verified");
} catch (error) {
  console.error(`Android release preparation failed: ${error.message}`);
  process.exitCode = 1;
}
