const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { effectiveStatus, jobError, statuses } = require(join(process.env.VEILWAY_AUTH_MODULES, "profilePresentation.js"));
const { profilesApi, ApiError, errorMessage } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));

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
