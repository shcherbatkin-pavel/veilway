const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const root = process.env.VEILWAY_AUTH_MODULES;
const { presentHealth, problemsFirst, failureCode } = require(join(root, "healthPresentation.js"));
const resource = require(join(root, "useResource.js"));
const { Health } = require(join(root, "Health.js"));

function overview(crl = {}) {
  return {
    generated_at: "2026-10-13T09:00:00Z", assessment: "ok", nodes: [], workers: [], operations: [],
    crl: { version: 1, next_update: null, publisher_error: null, nodes: [], assessment: "ok",
      attempted_at: null, observation_stale: false, publication_expired: false, ...crl },
  };
}
function node(overrides = {}) {
  return { slug: "aws-direct", state: "healthy", assessment: "ok", reasons: [], containers: { vpn: "healthy" },
    last_heartbeat_at: null, heartbeat_age_seconds: 5, uptime_seconds: 600, ...overrides };
}
function worker(overrides = {}) {
  return { name: "profile-worker", assessment: "ok", state: "running", started_at: null,
    completed_at: null, succeeded_at: null, in_progress: false, error_code: null, slow: false, ...overrides };
}
function operation(overrides = {}) {
  return { kind: "issue", assessment: "ok", queued: 0, running: 0, oldest_pending_age_seconds: null,
    delayed: 0, needs_review: 0, failed_last_day: 0, pki_errors: 0, awaiting_delivery: 0, ...overrides };
}
function freeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function render(t, data, error = "", loading = false) {
  t.mock.method(resource, "useResource", () => ({ data, error, loading, refresh: async () => {} }));
  return renderToStaticMarkup(React.createElement(Health, { onLogout: () => {}, onSection: () => {} }));
}

test("publication failures outrank missing or stale observations", () => {
  for (const [crl, assessment] of [
    [{}, "ok"], [{ version: 0 }, "ok"], [{ version: null }, "unknown"], [{ observation_stale: true }, "unknown"],
    [{ publication_expired: true, observation_stale: true, version: null }, "attention"],
    [{ publisher_error: "publication_unavailable", observation_stale: true, version: null }, "attention"],
  ]) assert.equal(presentHealth(overview(crl)).publication.assessment, assessment);
  const snapshot = presentHealth(overview({ publisher_error: "publication_unavailable", publication_expired: true, observation_stale: true, version: null }));
  assert.deepEqual(snapshot.publication.messages, ["Публикация отзывов недоступна", "Срок действия публикации истёк",
    "Нет свежего результата проверки публикации. Доступность PKI не подтверждена", "Нет публикации"]);
});

test("planned restarts suppress container warnings in explanations without removing details", () => {
  const data = overview();
  data.nodes = [node({ assessment: "attention", reasons: ["planned_restart"], containers: { vpn: "missing" } })];
  const snapshot = presentHealth(data);
  assert.deepEqual(snapshot.nodes[0].messages, ["Выполняется плановый перезапуск"]);
  assert.deepEqual(snapshot.nodes[0].containers, { vpn: "missing" });
  data.nodes[0].reasons = ["heartbeat_stale", "future-reason"];
  assert.deepEqual(presentHealth(data).nodes[0].messages, ["Сигнал от узла устарел", "Состояние требует проверки", "vpn: Отсутствует"]);
});

test("delivery states distinguish confirmed installation, problems and missing evidence", () => {
  for (const [status, assessment] of [
    ["current", "ok"], ["pending", "attention"], ["expired", "attention"], ["offline", "attention"],
    ["error", "attention"], ["unconfigured", "unknown"], ["future-status", "unknown"],
  ]) {
    const snapshot = presentHealth(overview({ nodes: [{ slug: "aws-direct", status, error_code: "installation_failed" }] }));
    assert.equal(snapshot.deliveries[0].assessment, assessment);
    assert.deepEqual(snapshot.deliveries[0].errorMessages, ["Не удалось установить список отзывов"]);
    assert.equal(snapshot.problems.length, assessment === "ok" ? 0 : 1);
  }
});

test("problem links match detail IDs and sections, preserve input and sort stably", () => {
  const data = overview({ observation_stale: true, nodes: [{ slug: "yc-direct", status: "pending", error_code: null }] });
  data.nodes = [node(), node({ slug: "yc-direct", assessment: "unknown", reasons: ["heartbeat_missing"], containers: null })];
  data.workers = [worker({ assessment: "attention", slow: true, error_code: "worker_failed" })];
  data.operations = [operation({ assessment: "unknown" }), operation({ kind: "restart", assessment: "attention", delayed: 1 })];
  const original = structuredClone(data);
  const snapshot = presentHealth(freeze(data));
  assert.deepEqual(data, original);
  const sorted = problemsFirst(snapshot.problems);
  assert.deepEqual(sorted.map(item => [item.id, item.section]), [
    ["health-worker-profile-worker", "history"], ["health-delivery-yc-direct", "nodes"], ["health-operation-restart", "nodes"],
    ["health-node-yc-direct", "nodes"], ["health-publication", "nodes"], ["health-operation-issue", "profiles"],
  ]);
  const rows = [...snapshot.nodes, ...snapshot.workers, snapshot.publication, ...snapshot.deliveries, ...snapshot.operations];
  for (const problem of snapshot.problems) assert.deepEqual(problem.messages, rows.find(row => row.id === problem.id).messages);
  assert.equal(snapshot.problems[0].id, "health-node-yc-direct");
  assert.deepEqual(snapshot.workers[0].messages, ["Фоновая задача завершилась ошибкой", "Проход длится больше трёх минут"]);
});

test("worker and operation explanations retain all independent warnings", () => {
  const data = overview();
  data.workers = [worker({ name: "future-worker", assessment: "unknown", state: "starting" })];
  data.operations = [operation({ kind: "revoke", assessment: "unknown", delayed: 2, needs_review: 3, failed_last_day: 4, pki_errors: 5 })];
  const snapshot = presentHealth(data);
  assert.equal(snapshot.workers[0].title, "Фоновая задача");
  assert.deepEqual(snapshot.workers[0].messages, ["Запускается. Нет свежего подтверждения завершённого прохода"]);
  assert.deepEqual(snapshot.operations[0].messages, ["Задержавшиеся: 2", "Требуют ручной проверки: 3", "Ошибки за последние сутки: 4",
    "Ожидают повтора после ошибки PKI: 5", "Недостаточно данных об операциях"]);
});

test("unknown error codes stay hidden in the model and rendered details", (t) => {
  const unknown = "synthetic-private-error";
  assert.equal(failureCode(null), null);
  assert.equal(failureCode(""), null);
  assert.equal(failureCode("worker_failed"), "worker_failed");
  for (const code of [unknown, "constructor", "__proto__"]) {
    assert.equal(failureCode(code), "Неизвестный код");
    const data = overview({ publisher_error: code, nodes: [{ slug: "yc-direct", status: "error", error_code: code }] });
    data.workers = [worker({ assessment: "attention", error_code: code })];
    const snapshot = presentHealth(data);
    assert.deepEqual(snapshot.publication.messages, ["Операция завершилась ошибкой"]);
    assert.deepEqual(snapshot.workers[0].messages, ["Операция завершилась ошибкой"]);
    const html = render(t, data);
    assert.match(html, /Неизвестный код/);
    assert.doesNotMatch(html, new RegExp(code));
  }
});

test("rendered health keeps stale snapshot warnings and withholds a healthy assessment", (t) => {
  const html = render(t, overview(), "Нет связи с панелью.");
  assert.match(html, /Показан устаревший снимок/);
  assert.match(html, /Свежих данных недостаточно для подтверждения состояния/);
  assert.match(html, /aria-label="Общая оценка"><span class="status-pill health-unknown"/);
  assert.doesNotMatch(html, /Проблем не обнаружено/);
  assert.match(render(t, overview()), /Проблем не обнаружено/);
  const empty = render(t, undefined, "Нет связи с панелью.", true);
  assert.match(empty, /Загружаем состояние/);
  assert.match(empty, /Не удалось получить состояние/);
  assert.doesNotMatch(empty, /health-summary/);
});
