const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { ApiError, request, listAll } = require(join(process.env.VEILWAY_AUTH_MODULES, "http.js"));
const { api, profilesApi, ApiError: PublicApiError } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));

test("JSON requests retain headers, body and cancellation with the session cache policy", async (t) => {
  const signal = new AbortController().signal;
  const headers = new Headers({ Accept: "application/json" });
  const body = JSON.stringify({ device_name: "Test device" });
  t.mock.method(global, "fetch", async (path, options) => {
    assert.equal(path, "/test");
    assert.equal(options.method, "PATCH");
    assert.equal(options.body, body);
    assert.equal(options.signal, signal);
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers.get("Accept"), "application/json");
    assert.equal(options.headers.get("Content-Type"), "application/json");
    assert.equal(options.headers.get("X-CSRF-Token"), "test-csrf");
    return new Response('{"id":"test-profile"}');
  });
  assert.deepEqual(await request("/test", { method: "PATCH", body, signal, headers, credentials: "omit", cache: "force-cache" }, "test-csrf"), { id: "test-profile" });
  assert.equal(headers.has("Content-Type"), false);
  assert.equal(headers.has("X-CSRF-Token"), false);
});

test("session reads omit CSRF and content type; logout accepts an empty 204", async (t) => {
  t.mock.method(global, "fetch", async (path, options) => {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers.has("Content-Type"), false);
    if (path.endsWith("/session")) {
      assert.equal(options.headers.has("X-CSRF-Token"), false);
      return new Response('{"role":"USER"}');
    }
    assert.equal(path, "/api/v1/auth/logout");
    assert.equal(options.method, "POST");
    assert.equal(options.headers.get("X-CSRF-Token"), "test-csrf");
    return new Response(null, { status: 204 });
  });
  assert.deepEqual(await api.session(), { role: "USER" });
  assert.equal(await api.logout("test-csrf"), undefined);
});

test("HTTP errors keep their public class, status and optional detail", async (t) => {
  assert.equal(ApiError, PublicApiError);
  for (const [body, message] of [['{"detail":"unavailable"}', "unavailable"], ["not JSON", "HTTP 503"], ["{}", "HTTP 503"]]) {
    t.mock.method(global, "fetch", async () => new Response(body, { status: 503 }));
    await assert.rejects(request("/test"), error => error instanceof PublicApiError && error.status === 503 && error.message === message);
  }
});

test("network failures and cancellation propagate unchanged", async (t) => {
  for (const error of [new TypeError("offline"), new DOMException("cancelled", "AbortError")]) {
    t.mock.method(global, "fetch", async () => { throw error; });
    await assert.rejects(request("/test"), actual => actual === error);
  }
});

test("a full final page requires an empty page; later errors reject the whole list", async (t) => {
  const signal = new AbortController().signal;
  const page = Array.from({ length: 100 }, (_, id) => ({ id }));
  const paths = [];
  t.mock.method(global, "fetch", async (path, options) => {
    assert.equal(options.signal, signal);
    paths.push(path);
    return new Response(JSON.stringify(paths.length === 1 ? page : []));
  });
  assert.deepEqual(await listAll("/test", signal), page);
  assert.deepEqual(paths, ["/test?limit=100&offset=0", "/test?limit=100&offset=100"]);
  t.mock.method(global, "fetch", async path => new Response(path.endsWith("offset=0") ? JSON.stringify(page) : "", { status: path.endsWith("offset=0") ? 200 : 401 }));
  await assert.rejects(listAll("/test", signal), error => error instanceof ApiError && error.status === 401);
});

test("failed downloads retain CSRF, cache policy and the fixed error message", async (t) => {
  t.mock.method(global, "fetch", async (path, options) => {
    assert.equal(path, "/api/v1/profiles/test-profile/download");
    assert.equal(options.method, "POST");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers.get("X-CSRF-Token"), "test-csrf");
    assert.equal(options.headers.has("Content-Type"), false);
    return new Response('{"detail":"synthetic-private-detail"}', { status: 403 });
  });
  await assert.rejects(profilesApi.download("test-profile", "test-csrf"), error => error instanceof ApiError && error.status === 403 && error.message === "download failed");
});

test("successful downloads save the response blob and revoke its temporary URL", async (t) => {
  const blob = new Blob(["synthetic-profile"]);
  const events = [];
  const link = { click: () => events.push("click"), remove: () => events.push("remove") };
  t.mock.method(global, "fetch", async () => ({ ok: true, blob: async () => blob }));
  t.mock.method(URL, "createObjectURL", value => {
    assert.equal(value, blob);
    return "blob:test-profile";
  });
  t.mock.method(URL, "revokeObjectURL", value => events.push(value));
  const previousDocument = Object.getOwnPropertyDescriptor(global, "document");
  Object.defineProperty(global, "document", { configurable: true, value: {
    createElement: tag => { assert.equal(tag, "a"); return link; },
    body: { appendChild: element => { assert.equal(element, link); events.push("append"); } },
  } });
  t.after(() => {
    if (previousDocument) Object.defineProperty(global, "document", previousDocument);
    else delete global.document;
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  await profilesApi.download("test-profile", "test-csrf");
  assert.equal(link.href, "blob:test-profile");
  assert.equal(link.download, "veilway-test-profile.ovpn");
  assert.deepEqual(events, ["append", "click", "remove"]);
  t.mock.timers.tick(9999);
  assert.equal(events.length, 3);
  t.mock.timers.tick(1);
  assert.deepEqual(events, ["append", "click", "remove", "blob:test-profile"]);
});
