const assert = require("node:assert/strict");
const { test } = require("node:test");
const { spawnSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const { mkdtempSync, writeFileSync, copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync } = require("node:fs");
const { join, resolve } = require("node:path");
const { tmpdir } = require("node:os");

test("release staging checks pinned hashes, missing files and extra published files", () => {
  const root = mkdtempSync(join(tmpdir(), "veilway-release-test-"));
  try {
    const source = join(root, "source"); mkdirSync(source);
    copyFileSync(resolve("stage-android-release.mjs"), join(root, "stage.mjs"));
    const files = {};
    for (const name of ["veilway-test.apk", "veilway-test-sources.tar.gz"]) {
      const data = Buffer.from(`synthetic ${name}`);
      writeFileSync(join(source, name), data);
      files[name] = createHash("sha256").update(data).digest("hex");
    }
    writeFileSync(join(root, "android-release.json"), JSON.stringify({ files }));
    const sums = Object.entries(files).map(([name, hash]) => `${hash}  ${name}\n`).join("");
    writeFileSync(join(source, "SHA256SUMS"), sums);
    writeFileSync(join(source, "private-extra"), "must not publish");
    const run = (...args) => spawnSync(process.execPath, [join(root, "stage.mjs"), ...args]);
    const staged = join(root, "staged");
    assert.equal(run("--stage", source, staged).status, 0);
    assert.deepEqual(readdirSync(staged).sort(), [...Object.keys(files), "SHA256SUMS"].sort());
    assert.equal(readFileSync(join(staged, "SHA256SUMS"), "utf8"), sums);
    assert.equal(run("--verify", staged).status, 0);
    writeFileSync(join(staged, "private-extra"), "extra");
    assert.notEqual(run("--verify", staged).status, 0);
    writeFileSync(join(source, "veilway-test.apk"), "tampered");
    assert.notEqual(run("--stage", source, join(root, "tampered")).status, 0);
    rmSync(join(source, "veilway-test.apk"));
    assert.notEqual(run("--stage", source, join(root, "missing")).status, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
