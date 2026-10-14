const assert = require("node:assert/strict");
const { test } = require("node:test");
const { join } = require("node:path");
const { prepareAnalyticsChart } = require(join(process.env.VEILWAY_AUTH_MODULES, "analyticsChart.js"));
const day = (day, observed, users = 0) => ({day, observed, users, profiles: users, sessions_started: users, bytes_received: users, bytes_sent: users});

test("missing observations split the line and never influence its scale", () => {
 const chart = prepareAnalyticsChart([day("2026-10-01", true, 2), day("2026-10-02", false, 999), day("2026-10-03", true, 4)], null, "users");
 assert.equal(chart.maximum, 4); assert.deepEqual(chart.segments, [["85,90"], ["680,25"]]);
 assert.equal(chart.observed.length, 2);
});
test("empty, unobserved and single-day series keep finite geometry", () => {
 for (const daily of [[], [day("2026-10-01", false)], [day("2026-10-01", true)]]) {
  const chart = prepareAnalyticsChart(daily, null, "users");
  assert.equal(chart.maximum, 1); assert.equal(chart.x(0), 85); assert.equal(chart.y(0), 155);
 }
});
test("collection boundary uses UTC day and traffic sums both directions", () => {
 const chart = prepareAnalyticsChart([day("2026-10-01", true, 10), day("2026-10-02", true, 3)], "2026-10-02T23:00:00Z", "traffic");
 assert.equal(chart.data.length, 1); assert.deepEqual(chart.values, [6]); assert.equal(chart.maximum, 6);
});
