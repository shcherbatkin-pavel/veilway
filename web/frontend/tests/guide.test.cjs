const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { ConnectionGuide } = require(join(process.env.VEILWAY_AUTH_MODULES, "ConnectionGuide.js"));

test("Android guide downloads own client and explains browser login and app routing", () => {
  const html = renderToStaticMarkup(React.createElement(ConnectionGuide, { onProfiles: () => {} }));
  for (const text of ["Скачать Veilway для Android", "Android 10", "Google", "YouTube", "Telegram", "OZON", "ВКонтакте", "следующем подключении", "самого браузера"]) assert.ok(html.includes(text), text);
  for (const name of ["veilway-0.1.2-pilot.apk", "veilway-0.1.2-pilot-sources.tar.gz", "SHA256SUMS"]) assert.ok(html.includes(`href="/downloads/android/${name}"`));
  assert.doesNotMatch(html, /OpenVPN Connect|Скачайте VPN-профиль|Импортируйте профиль|\.ovpn/);
  assert.equal((html.match(/<li>/g) || []).length, 4);
  for (const platform of ["iPhone", "Windows", "Linux"]) assert.ok(html.includes(platform));
});
