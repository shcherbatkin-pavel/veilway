const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { effectiveStatus, jobError, statuses } = require(join(process.env.VEILWAY_AUTH_MODULES, "profilePresentation.js"));
const { profilesApi, ApiError, errorMessage } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));
const { indexProfiles } = require(join(process.env.VEILWAY_AUTH_MODULES, "profileIndexes.js"));
const { selectProfiles } = require(join(process.env.VEILWAY_AUTH_MODULES, "profileSelection.js"));

const selectionTime = Date.parse("2026-10-09T12:00:00Z");
const selectionProfiles = Object.freeze([
  { id: "a", device_name: "Рабочий ноутбук", mode: "aws-direct", owner_id: "u1", status: "active", expires_at: "2027-01-01T00:00:00Z" },
  { id: "b", device_name: "Личный телефон", mode: "yc-direct", owner_id: "u2", status: "active", expires_at: "2027-01-01T00:00:00Z" },
  { id: "c", device_name: "Старый ноутбук", mode: "aws-direct", owner_id: "u1", status: "active", expires_at: "2026-10-09T12:00:00Z" },
  { id: "d", device_name: "Планшет", mode: "yc-aws-multihop", owner_id: null, status: "issuing", expires_at: "2027-01-01T00:00:00Z" },
  { id: "e", device_name: "Запасной телефон", mode: "yc-direct", owner_id: "missing", status: "revoking", expires_at: "2026-01-01T00:00:00Z" },
].map(profile => Object.freeze(profile)));

function selection(filters = {}, context = {}) {
  return selectProfiles(selectionProfiles, { query: "", status: "", mode: "", owner: "", ...filters }, {
    admin: true, now: selectionTime,
    usersById: new Map([["u1", { id: "u1", email: "owner@example.invalid" }]]), ...context,
  });
}
const ids = profiles => profiles.map(profile => profile.id);

test("combined filters preserve order and summary counts describe the whole owner scope", () => {
  const result = selection({ query: "НОУТБУК", status: "active", mode: "aws-direct", owner: "u1" });
  assert.deepEqual(ids(result.visible), ["a"]);
  assert.deepEqual(ids(result.scoped), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(result.counts, { total: 5, active: 2, pending: 2 });
  assert.deepEqual(ids(selection({ query: "ноутбук", mode: "aws-direct" }).visible), ["a", "c"]);
});

test("expiry boundary affects active and expired filters but retains revocation state", () => {
  assert.deepEqual(ids(selection({ status: "expired" }).visible), ["c"]);
  assert.deepEqual(ids(selection({ status: "active" }, { now: selectionTime - 1 }).visible), ["a", "b", "c"]);
  assert.deepEqual(ids(selection({ status: "revoking" }).visible), ["e"]);
  assert.equal(selection({}, { now: selectionTime - 1 }).counts.active, 3);
});

test("owner filters support unassigned and missing owners and cannot escape onlyOwner", () => {
  assert.deepEqual(ids(selection({ owner: "unassigned" }).visible), ["d"]);
  assert.deepEqual(ids(selection({ query: "владелец недоступен" }).visible), ["b", "e"]);
  const result = selection({ owner: "u2" }, { onlyOwner: "u1" });
  assert.deepEqual(ids(result.visible), []);
  assert.deepEqual(ids(result.scoped), ["a", "c"]);
  assert.deepEqual(result.counts, { total: 2, active: 1, pending: 0 });
});

test("USER search excludes owner metadata and ignores the administrative owner filter", () => {
  assert.deepEqual(ids(selection({ query: "OWNER@EXAMPLE.INVALID" }).visible), ["a", "c"]);
  assert.deepEqual(ids(selection({ query: "owner@example.invalid" }, { admin: false }).visible), []);
  assert.deepEqual(ids(selection({ owner: "u2" }, { admin: false, onlyOwner: "u1" }).visible), ["a", "c"]);
  assert.deepEqual(ids(selection({ query: "YANDEX → AWS" }, { admin: false }).visible), ["d"]);
});

test("empty and filtered-empty results remain distinguishable without changing inputs", () => {
  const noMatch = selection({ query: "no matching device" });
  assert.equal(noMatch.visible.length, 0);
  assert.equal(noMatch.scoped.length, 5);
  assert.equal(selection({ query: "  ноутбук" }).visible.length, 0);
  const emptyScope = selection({}, { onlyOwner: "absent" });
  assert.deepEqual(emptyScope, { scoped: [], visible: [], counts: { total: 0, active: 0, pending: 0 } });
  assert.equal(selectionProfiles[2].status, "active");
});

test("snapshot indexes preserve first-job selection and owner counts", () => {
  const profiles = [{ id: "p1", owner_id: "u1" }, { id: "p2", owner_id: "u1" }, { id: "p3", owner_id: null }];
  const jobs = [
    { id: "new-issue", profile_id: "p1", kind: "issue" },
    { id: "revoke", profile_id: "p1", kind: "revoke" },
    { id: "old-issue", profile_id: "p1", kind: "issue" },
  ];
  const users = [{ id: "u1", email: "test@example.invalid" }];
  const indexes = indexProfiles(profiles, jobs, users);
  for (const kind of ["issue", "revoke"]) {
    assert.equal(indexes.jobsByProfileAndKind.get(`p1:${kind}`), jobs.find(job => job.profile_id === "p1" && job.kind === kind));
  }
  assert.equal(indexes.countsByOwner.get("u1"), 2);
  assert.equal(indexes.profilesById.get("p2"), profiles[1]);
  assert.equal(indexes.usersById.get("u1"), users[0]);
  const next = indexProfiles([{ id: "p2", owner_id: "u2" }]);
  assert.equal(next.countsByOwner.get("u1"), undefined);
  assert.equal(next.countsByOwner.get("u2"), 1);
  assert.equal(next.jobsByProfileAndKind.size, 0);
});

test("expiry disables an active profile without hiding revocation state", () => {
  const profile = { status: "active", expires_at: "2026-10-05T10:00:00Z" };
  assert.equal(effectiveStatus(profile, Date.parse(profile.expires_at)), "expired");
  assert.equal(effectiveStatus({ ...profile, status: "revoking" }, Date.parse(profile.expires_at)), "revoking");
  assert.equal(statuses.revoking, "Отзыв применяется");
});
test("a failed issue explains the CA limit to ADMIN and sends USER to administrator", () => {
  const job = { status: "failed", error_code: "pki_expiry_rejected" };
  assert.match(jobError(job, true), /меньшим сроком/);
  assert.equal(jobError(job, false), "Не удалось выпустить профиль. Обратитесь к администратору.");
  assert.match(jobError({ status: "queued", error_code: "pki_unavailable" }), /автоматически/);
});
test("errors never expose server-provided bodies", () => {
  for (const status of [403, 404, 409, 422, 503, 500]) assert.doesNotMatch(errorMessage(new ApiError(status, "synthetic-secret-error-body")), /synthetic-secret/);
});
test("profile reads paginate and preserve the cancellation signal", async () => {
  const original = global.fetch, controller = new AbortController(), calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => url.includes("offset=0") ? Array.from({ length: 100 }, (_, id) => ({ id })) : [{ id: 100 }] };
  };
  try {
    assert.equal((await profilesApi.profiles(controller.signal)).length, 101);
    assert.deepEqual(calls.map(call => call.url), ["/api/v1/profiles?limit=100&offset=0", "/api/v1/profiles?limit=100&offset=100"]);
    assert.ok(calls.every(call => call.options.signal === controller.signal && call.options.cache === "no-store"));
  } finally { global.fetch = original; }
});
