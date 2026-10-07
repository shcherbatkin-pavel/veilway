import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const output = mkdtempSync(join(tmpdir(), "veilway-polling-test."));
try {
  execFileSync(join(root, "node_modules/.bin/tsc"), [
    "src/polling.ts", "src/Login.tsx", "src/SessionView.tsx", "src/profilePresentation.ts",
    "--target", "ES2022", "--module", "commonjs", "--jsx", "react-jsx",
    "--moduleResolution", "node", "--strict", "--skipLibCheck", "--outDir", output,
  ], { cwd: root, stdio: "inherit" });
  execFileSync(process.execPath, ["--test", "tests/polling.test.cjs", "tests/auth.test.cjs", "tests/profiles.test.cjs"], {
    cwd: root, stdio: "inherit",
    env: {
      ...process.env, VEILWAY_POLLING_MODULE: join(output, "polling.js"),
      VEILWAY_AUTH_MODULES: output, NODE_PATH: join(root, "node_modules"),
    },
  });
} finally {
  rmSync(output, { recursive: true, force: true });
}
