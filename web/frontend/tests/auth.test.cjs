const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const { Login } = require(join(process.env.VEILWAY_AUTH_MODULES, "Login.js"));
const { SessionView } = require(join(process.env.VEILWAY_AUTH_MODULES, "SessionView.js"));

test("Google sign-in navigates to the server and has no password form", () => {
  const html = renderToStaticMarkup(React.createElement(Login));
  assert.match(html, /href="\/api\/v1\/auth\/google\/start"/);
  assert.match(html, /Войти через Google/);
  assert.doesNotMatch(html, /<form|<input|password/);
});

test("login errors are understandable and never echo the provider payload", () => {
  for (const code of ["cancelled", "unavailable", "test-secret-provider-payload"]) {
    const html = renderToStaticMarkup(React.createElement(Login, { errorCode: code }));
    assert.match(html, /role="alert"/);
    assert.doesNotMatch(html, /test-secret-provider-payload/);
  }
});

test("USER sees an account view without infrastructure controls or requests", () => {
  const originalFetch = global.fetch;
  global.fetch = () => { throw new Error("USER must not fetch infrastructure"); };
  try {
    const html = renderToStaticMarkup(React.createElement(SessionView, {
      session: { user_id: "test-user", email: "user@example.invalid", role: "USER", csrf_token: "test-csrf" },
      onLogout: () => {},
    }));
    assert.match(html, /Мои профили/);
    assert.match(html, /user@example.invalid/);
    assert.doesNotMatch(html, /Перезагрузить|VPN-узлы|LIVE STATUS/);
  } finally { global.fetch = originalFetch; }
});

test("ADMIN retains infrastructure controls", () => {
  const html = renderToStaticMarkup(React.createElement(SessionView, {
    session: { user_id: "test-admin", email: "admin@example.invalid", role: "ADMIN", csrf_token: "test-csrf" },
    onLogout: () => {},
  }));
  assert.match(html, /Профили/);
  assert.match(html, /Пользователи/);
  assert.match(html, /Узлы/);
  assert.match(html, /История/);
});
