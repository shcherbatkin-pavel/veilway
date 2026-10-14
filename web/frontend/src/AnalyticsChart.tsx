import { useState } from "react";
import type { BusinessOverview } from "./api";
import { ChartMetric, chartLabels, prepareAnalyticsChart } from "./analyticsChart";
import { bytes, number } from "./analyticsFormatting";
import { Help } from "./AnalyticsElements";

export function AnalyticsChart({ data: daily, start, metric, onMetric }: { data: BusinessOverview["daily"]; start: string | null; metric: ChartMetric; onMetric: (metric: ChartMetric) => void }) {
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const { data, observed, values, maximum, x, y, segments } = prepareAnalyticsChart(daily, start, metric);
  const labels = chartLabels;
  const format = (value: number) => metric === "traffic" ? bytes(value) : number(value);
  const selected = data.findIndex(d => d.day === selectedDay);
  const index = selected >= 0 ? selected : data.length - 1;
  const chosen = data[index];
  return <section className="analytics-panel"><div className="section-heading"><h2>Использование по дням</h2><label><span className="sr-only">Показатель графика</span><select aria-label="Показатель графика" value={metric} onChange={e => onMetric(e.target.value as ChartMetric)}>{Object.entries(labels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label></div>
    {observed.length > 1 ? <>
      <svg className="analytics-chart" viewBox="0 0 720 190" role="img" aria-label={`${labels[metric]} по дням, UTC`} onPointerMove={e => { const matrix = e.currentTarget.getScreenCTM(); if (!matrix) return; const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse()); const i = Math.max(0, Math.min(data.length - 1, Math.round((point.x - 85) / 595 * (data.length - 1)))); setSelectedDay(data[i]?.day ?? null); }} onPointerLeave={() => setSelectedDay(null)}>
        {[0, maximum / 2, maximum].map((value, i) => <g key={i}><line x1="85" y1={y(value)} x2="680" y2={y(value)} stroke="var(--line)" /><text x="75" y={y(value) + 4} textAnchor="end">{format(value)}</text></g>)}
        {segments.filter(s => s.length).map((points, i) => <polyline key={i} points={points.join(" ")} fill="none" stroke="var(--accent)" strokeWidth="2" />)}
        {data.map((d, i) => d.observed && <circle key={d.day} cx={x(i)} cy={y(values[i])} r={index === i ? 4 : 2} fill="var(--accent)"><title>{d.day}: {format(values[i])}</title></circle>)}
        <text x="85" y="180">{data[0]?.day}</text><text x="680" y="180" textAnchor="end">{data[data.length - 1]?.day}</text>
      </svg>
      {chosen && <div className="analytics-chart-caption"><span>{chosen.day} · UTC</span><strong>{chosen.observed ? format(values[index]) : "Нет наблюдений"}</strong></div>}
      <label className="analytics-day"><span>День графика</span><input aria-label="День графика" type="range" min="0" max={Math.max(0, data.length - 1)} value={index} onChange={e => setSelectedDay(data[Number(e.target.value)]?.day ?? null)} /></label>
    </> : observed.length === 1 ? <div className="analytics-short-history" role="status"><span>{observed[0].day} · UTC</span><strong>{format(metric === "traffic" ? observed[0].bytes_received + observed[0].bytes_sent : observed[0][metric])}</strong><p className="muted">Пока наблюдался только один день. График появится, когда накопятся данные.</p></div> : <p className="empty-state">За выбранный период наблюдений нет.</p>}
    <Help>Дни считаются в UTC. Разрывы означают отсутствие наблюдений; неполное покрытие источников может занижать показатели. Трафик относится к дню считывания счётчиков.</Help>
  </section>;
}
