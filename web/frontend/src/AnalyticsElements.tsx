import type { ReactNode } from "react";

export function Metric({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return <article className="analytics-metric"><span>{title}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>;
}
export function Help({ children }: { children: ReactNode }) {
  return <details className="analytics-help"><summary>Как считается</summary><div className="muted">{children}</div></details>;
}
export function Facts({ items }: { items: [string, string][] }) {
  return <dl className="analytics-facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
export function Pagination({ offset, total, change, label }: { offset: number; total: number; change: (offset: number) => void; label: string }) {
  return <div className="analytics-pagination"><span>{total ? `${offset + 1}–${Math.min(offset + 50, total)} из ${total}` : "Нет записей"}</span><div className="actions"><button className="secondary" aria-label={`Предыдущие ${label}`} disabled={offset === 0} onClick={() => change(Math.max(0, offset - 50))}>Назад</button><button className="secondary" aria-label={`Следующие ${label}`} disabled={offset + 50 >= total} onClick={() => change(offset + 50)}>Далее</button></div></div>;
}
