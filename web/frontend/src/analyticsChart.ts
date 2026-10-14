import type { BusinessOverview } from "./api";

export type ChartMetric = "users" | "profiles" | "sessions_started" | "traffic";
export const chartLabels = { users: "Пользователи", profiles: "Профили", sessions_started: "Начатые сессии", traffic: "Трафик" };

export function prepareAnalyticsChart(daily: BusinessOverview["daily"], start: string | null, metric: ChartMetric) {
  const data = start ? daily.filter(d => d.day >= start.slice(0, 10)) : daily;
  const observed = data.filter(d => d.observed);
  const values = data.map(d => metric === "traffic" ? d.bytes_received + d.bytes_sent : d[metric]);
  const maximum = Math.max(1, ...data.map((d, i) => d.observed ? values[i] : 0));
  const x = (index: number) => 85 + index * 595 / Math.max(1, data.length - 1);
  const y = (value: number) => 155 - 130 * value / maximum;
  const segments: string[][] = [[]];
  data.forEach((d, i) => { if (d.observed) segments[segments.length - 1].push(`${x(i)},${y(values[i])}`); else if (segments[segments.length - 1].length) segments.push([]); });
  return { data, observed, values, maximum, x, y, segments };
}
