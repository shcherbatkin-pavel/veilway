const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { buildAnalyticsRequest, loadAnalytics } = require(join(process.env.VEILWAY_AUTH_MODULES, "analyticsResource.js"));
const { analyticsApi, ApiError } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const resource = require(join(process.env.VEILWAY_AUTH_MODULES, "useResource.js"));
const { Analytics } = require(join(process.env.VEILWAY_AUTH_MODULES, "Analytics.js"));

function request(filters = {}) {
  return buildAnalyticsRequest({ view: "overview", days: 90, mode: "", onlineOnly: false,
    profileOffset: 100, sessionOffset: 150, selected: null, ...filters });
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test("each view preserves its period, filters, pagination and snapshot key", () => {
  for (const [filters, overviewQuery, detailQuery] of [
    [{}, "days=30", "days=30&online_only=true&limit=8&offset=0"],
    [{ view: "profiles", onlineOnly: true, mode: "aws-direct" }, "days=90&mode=aws-direct", "days=90&mode=aws-direct&online_only=true&limit=50&offset=100"],
    [{ view: "profiles" }, "days=90", "days=90&online_only=false&limit=50&offset=100"],
    [{ view: "sessions", selected: { id: "test-profile" } }, "days=90", "days=90&limit=50&offset=150&profile_id=test-profile"],
    [{ view: "sessions" }, "days=90", "days=90&limit=50&offset=150"],
    [{ view: "activity", mode: "yc-aws-multihop" }, "days=90&mode=yc-aws-multihop", ""],
  ]) {
    const actual = request(filters);
    assert.deepEqual(actual, { view: filters.view ?? "overview", overviewQuery, detailQuery,
      key: `${filters.view ?? "overview"}:${overviewQuery}:${detailQuery}` });
  }
});

test("snapshot keys change for effective filters and ignore hidden filters", () => {
  const base = request();
  assert.equal(request({ days: 7, onlineOnly: true, profileOffset: 0, sessionOffset: 0, selected: { id: "test-profile" } }).key, base.key);
  assert.notEqual(request({ mode: "yc-direct" }).key, base.key);
  const profiles = request({ view: "profiles" });
  for (const change of [{ days: 7 }, { onlineOnly: true }, { profileOffset: 0 }]) {
    assert.notEqual(request({ view: "profiles", ...change }).key, profiles.key);
  }
  const sessions = request({ view: "sessions" });
  assert.notEqual(request({ view: "sessions", selected: { id: "test-profile" } }).key, sessions.key);
  assert.notEqual(request({ view: "sessions", sessionOffset: 0 }).key, sessions.key);
  assert.equal(request({ view: "activity", profileOffset: 0, selected: { id: "test-profile" } }).key, request({ view: "activity" }).key);
});

test("each view loads only its required endpoints with the same cancellation signal", async (t) => {
  for (const view of ["overview", "profiles", "sessions", "activity"]) {
    const query = request({ view });
    const calls = [];
    const signal = new AbortController().signal;
    const overview = { generated_at: "synthetic-observation" };
    const profiles = { items: [], total: 0 };
    const sessions = { items: [], total: 0, retained_since: "synthetic-retention" };
    for (const [name, result] of Object.entries({ overview, profiles, sessions })) {
      t.mock.method(analyticsApi, name, async (value, actualSignal) => {
        calls.push([name, value]);
        assert.equal(actualSignal, signal);
        return result;
      });
    }
    const snapshot = await loadAnalytics(query, signal);
    const detail = view === "overview" || view === "profiles" ? "profiles" : view === "sessions" ? "sessions" : null;
    assert.deepEqual(calls, [["overview", query.overviewQuery], ...(detail ? [[detail, query.detailQuery]] : [])]);
    assert.deepEqual(snapshot, { key: query.key, overview, ...(detail ? { [detail]: detail === "profiles" ? profiles : sessions } : {}) });
  }
});

test("grouped loads wait for both responses and retain their association", async (t) => {
  const overview = deferred(), profiles = deferred();
  t.mock.method(analyticsApi, "overview", () => overview.promise);
  t.mock.method(analyticsApi, "profiles", () => profiles.promise);
  let settled = false;
  const query = request();
  const pending = loadAnalytics(query, new AbortController().signal).finally(() => { settled = true; });
  profiles.resolve({ items: [{ profile_id: "test-profile" }], total: 1 });
  await flush();
  assert.equal(settled, false);
  overview.resolve({ generated_at: "synthetic-observation" });
  assert.deepEqual(await pending, { key: query.key, overview: { generated_at: "synthetic-observation" }, profiles: { items: [{ profile_id: "test-profile" }], total: 1 } });
});

test("revoked admin access outranks 503 on either endpoint in every grouped view", async (t) => {
  for (const view of ["overview", "profiles", "sessions"]) {
    for (const statuses of [[503, 403], [403, 503], [503, 401], [401, 503]]) {
      const detail = view === "sessions" ? "sessions" : "profiles";
      t.mock.method(analyticsApi, "overview", async () => { throw new ApiError(statuses[0], "synthetic-error"); });
      t.mock.method(analyticsApi, detail, async () => { throw new ApiError(statuses[1], "synthetic-error"); });
      await assert.rejects(loadAnalytics(request({ view }), new AbortController().signal), error => error instanceof ApiError && error.status === 401);
    }
  }
  t.mock.method(analyticsApi, "overview", async () => { throw new ApiError(403, "synthetic-error"); });
  await assert.rejects(loadAnalytics(request({ view: "activity" }), new AbortController().signal), error => error instanceof ApiError && error.status === 401);
});

test("failed groups wait for the remaining request and propagate ordinary failures unchanged", async (t) => {
  const slow = deferred();
  const failure = new ApiError(503, "unavailable");
  t.mock.method(analyticsApi, "overview", async () => { throw failure; });
  t.mock.method(analyticsApi, "profiles", () => slow.promise);
  let settled = false;
  const pending = assert.rejects(loadAnalytics(request(), new AbortController().signal), error => error === failure).then(() => { settled = true; });
  await flush();
  assert.equal(settled, false);
  slow.resolve({ items: [], total: 0 });
  await pending;
  const offline = new TypeError("offline");
  t.mock.method(analyticsApi, "overview", async () => { throw offline; });
  await assert.rejects(loadAnalytics(request({ view: "activity" }), new AbortController().signal), error => error === offline);
});

test("aborting a grouped load cancels both requests without yielding a partial snapshot", async (t) => {
  const controller = new AbortController();
  const aborted = [];
  const failure = new DOMException("cancelled", "AbortError");
  for (const name of ["overview", "sessions"]) {
    t.mock.method(analyticsApi, name, (_, signal) => new Promise((resolve, reject) => {
      signal.addEventListener("abort", () => { aborted.push(name); reject(failure); }, { once: true });
    }));
  }
  const pending = loadAnalytics(request({ view: "sessions" }), controller.signal);
  controller.abort();
  await assert.rejects(pending, error => error === failure);
  assert.deepEqual(aborted, ["overview", "sessions"]);
});

test("the component binds its selected filters and records only non-cancelled failures", async (t) => {
  const filters = { view: "sessions", days: 7, mode: "yc-direct", onlineOnly: false,
    profileOffset: 50, sessionOffset: 100, selected: { id: "test-profile", name: "Test", from: "profiles" } };
  const query = request(filters);
  const state = [filters.view, filters.days, filters.mode, filters.onlineOnly, filters.profileOffset,
    filters.sessionOffset, filters.selected, "users", ""];
  let index = 0, load;
  const changes = [];
  t.mock.method(React, "useState", () => {
    const position = index++;
    return [state[position], value => changes.push([position, value])];
  });
  const logout = () => {};
  t.mock.method(resource, "useResource", (callback, onLogout) => {
    load = callback;
    assert.equal(onLogout, logout);
    return { data: undefined, error: "", refresh: async () => {} };
  });
  renderToStaticMarkup(React.createElement(Analytics, { onLogout: logout }));
  const signal = new AbortController().signal;
  t.mock.method(analyticsApi, "overview", async (value, actualSignal) => {
    assert.equal(value, query.overviewQuery);
    assert.equal(actualSignal, signal);
    return { generated_at: "synthetic-observation" };
  });
  t.mock.method(analyticsApi, "sessions", async value => {
    assert.equal(value, query.detailQuery);
    return { items: [], total: 0 };
  });
  assert.equal((await load(signal)).key, query.key);
  const failure = new TypeError("offline");
  t.mock.method(analyticsApi, "overview", async () => { throw failure; });
  await assert.rejects(load(signal), error => error === failure);
  assert.deepEqual(changes, [[8, query.key]]);
  changes.length = 0;
  const cancelled = new AbortController();
  cancelled.abort();
  await assert.rejects(load(cancelled.signal), error => error === failure);
  assert.deepEqual(changes, []);
});

test("the component hides snapshots and errors belonging to previous filters", (t) => {
  const key = request({ days: 30 }).key;
  const data = { key: "previous-filters", overview: { generated_at: "previous-observation" } };
  const error = "synthetic-previous-error";
  t.mock.method(resource, "useResource", () => ({ data, error, refresh: async () => {} }));
  let html = renderToStaticMarkup(React.createElement(Analytics, { onLogout() {} }));
  assert.match(html, /Загружаем аналитику/);
  assert.doesNotMatch(html, /previous-observation|synthetic-previous-error/);
  let index = 0;
  t.mock.method(React, "useState", initial => [index++ === 8 ? key : initial, () => {}]);
  html = renderToStaticMarkup(React.createElement(Analytics, { onLogout() {} }));
  assert.match(html, /synthetic-previous-error/);
  assert.match(html, /Свежие данные недоступны/);
  assert.doesNotMatch(html, /previous-observation|Загружаем аналитику/);
});
