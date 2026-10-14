#!/usr/bin/env node
// Use the installed compiler: no code generation and no additional dependencies.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "web/frontend/package.json"));
const ts = require("typescript");
const contract = Object.fromEntries(fs.readFileSync(path.join(root,
  "web/backend/tests/fixtures/client-contract.properties"), "utf8")
  .split(/\r?\n/).filter(line => line && !line.startsWith("#"))
  .map(line => line.split("=")));
const configFile = path.join(root, "web/frontend/tsconfig.app.json");
const config = ts.readConfigFile(configFile, ts.sys.readFile);
assert.equal(config.error, undefined);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(configFile));
assert.equal(parsed.errors.length, 0);
const program = ts.createProgram(parsed.fileNames, parsed.options);
assert.equal(ts.getPreEmitDiagnostics(program).length, 0, "frontend must typecheck");
const checker = program.getTypeChecker();
const source = program.getSourceFile(path.join(root, "web/frontend/src/api.ts"));
const exports = new Map(checker.getExportsOfModule(checker.getSymbolAtLocation(source))
  .map(symbol => [symbol.name, checker.getDeclaredTypeOfSymbol(symbol)]));
function check(name, field, key) {
  let type = exports.get(name);
  assert.ok(type, `missing ${name}`);
  if (field) {
    const property = type.getProperty(field);
    assert.ok(property, `missing ${name}.${field}`);
    type = checker.getTypeOfSymbolAtLocation(property, source);
  }
  const members = type.isUnion() ? type.types : [type];
  assert.ok(members.every(item => item.flags & ts.TypeFlags.StringLiteral), `${name}.${field} must remain literal`);
  assert.deepEqual(members.map(item => item.value).sort(), contract[key].split(",").sort(), `${name}.${field}`);
}
check("ProfileMode", null, "profile.modes");
check("ProfileStatus", null, "profile.statuses");
for (const name of ["Profile", "CreateProfile", "AnalyticsSource", "ProfileAnalytics", "SessionAnalytics"])
  check(name, "mode", "profile.modes");
check("Profile", "status", "profile.statuses");
check("Session", "role", "session.roles");
check("ProfileJob", "status", "job.statuses");
check("ProfileJob", "kind", "job.kinds");
console.log("Shared frontend client contracts passed");
