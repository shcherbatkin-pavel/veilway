const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { createPoller } = require(process.env.VEILWAY_POLLING_MODULE);
const { ApiError, together } = require(join(process.env.VEILWAY_AUTH_MODULES, "api.js"));

const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("polls every five seconds without overlap, including manual refresh", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const request = deferred();
  let calls = 0;
  const results = [];
  const poller = createPoller(() => { calls++; return request.promise; }, (value) => results.push(value), assert.fail);
  t.after(() => poller.stop());
  poller.start();
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(10000);
  const pending = poller.refresh();
  await flush();
  assert.equal(calls, 1);
  request.resolve("fresh");
  await pending;
  assert.deepEqual(results, ["fresh"]);
  t.mock.timers.tick(4999);
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(calls, 2);
});

for (const failure of [false, true]) {
  test(`stop aborts and suppresses a late ${failure ? "error" : "response"}`, async () => {
    const request = deferred();
    let signal;
    const results = [];
    const errors = [];
    const poller = createPoller((nextSignal) => { signal = nextSignal; return request.promise; }, (value) => results.push(value), (error) => errors.push(error));
    const pending = poller.refresh();
    await flush();
    poller.stop();
    assert.equal(signal.aborted, true);
    if (failure) request.reject(new Error("late failure"));
    else request.resolve("late data");
    await pending;
    await poller.refresh();
    assert.deepEqual(results, []);
    assert.deepEqual(errors, []);
  });
}

test("failed requests release the lock and pass 401 to the error handler", async () => {
  let calls = 0;
  const unauthorized = { status: 401 };
  const errors = [];
  const values = [];
  const poller = createPoller(async () => {
    if (++calls === 1) throw unauthorized;
    return "recovered";
  }, (value) => values.push(value), (error) => errors.push(error));
  await poller.refresh();
  await poller.refresh();
  poller.stop();
  assert.deepEqual(errors, [unauthorized]);
  assert.deepEqual(values, ["recovered"]);
});

test("immediate unmount does not start a request", async () => {
  const poller = createPoller(assert.fail, assert.fail, assert.fail);
  const pending = poller.refresh();
  poller.stop();
  await pending;
});

test("grouped results keep request order even when requests finish in reverse", async () => {
  const first = deferred();
  const second = deferred();
  const pending = together([first.promise, second.promise]);
  second.resolve("jobs");
  first.resolve("vms");
  assert.deepEqual(await pending, ["vms", "jobs"]);
});

test("a failed group waits for every request before allowing another poll", async () => {
  const slow = deferred();
  const unavailable = new ApiError(503, "unavailable");
  const errors = [];
  let calls = 0;
  const poller = createPoller(() => {
    calls++;
    return together([Promise.reject(unavailable), slow.promise]);
  }, assert.fail, error => errors.push(error));
  const pending = poller.refresh();
  await flush();
  const concurrent = poller.refresh();
  assert.equal(calls, 1);
  assert.deepEqual(errors, []);
  slow.resolve("jobs");
  await Promise.all([pending, concurrent]);
  assert.deepEqual(errors, [unavailable]);
  await poller.refresh();
  poller.stop();
  assert.equal(calls, 2);
});

test("an expired session takes precedence over other grouped failures", async () => {
  const unavailable = new ApiError(503, "unavailable");
  const unauthorized = new ApiError(401, "expired session");
  for (const failures of [[unavailable, unauthorized], [unauthorized, unavailable]]) {
    await assert.rejects(together(failures.map(error => Promise.reject(error))), error => error === unauthorized);
  }
  const networkError = new Error("network failure");
  await assert.rejects(together([Promise.reject(networkError), Promise.reject(unavailable)]), error => error === networkError);
});
