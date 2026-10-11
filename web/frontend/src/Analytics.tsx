import { ReactNode, useMemo, useRef, useState } from "react";
import { analyticsApi, ApiError, BusinessOverview, ProfileAnalytics, SessionAnalytics, ProfileMode, together } from "./api";
import { useResource } from "./useResource";

const modeNames: Record<ProfileMode, string> = { "aws-direct": "AWS напрямую", "yc-direct": "Yandex напрямую", "yc-aws-multihop": "Yandex → AWS" };
const sourceNames = { unconfigured: "Сбор не настроен", unavailable: "Сбор недоступен", stale: "Данные устарели", fresh: "Свежие данные" };
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("ru-RU");
const date = (value: string | null) => value ? new Date(value).toLocaleString("ru-RU") : "Не наблюдалось";
function bytes(value: number) {
  const units = ["Б", "КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}
function duration(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${Math.floor(value)} с`;
  if (value < 3600) return `${Math.floor(value / 60)} мин`;
  return `${Math.floor(value / 3600)} ч ${Math.floor(value % 3600 / 60)} мин`;
}
function Metric({ title, value, hint }: { title: string; value: string; hint?: string }) {
  return <article className="analytics-metric"><span>{title}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>;
}
type View = "overview" | "activity" | "profiles" | "sessions";
type ChartMetric = "users" | "profiles" | "sessions_started" | "traffic";
const tabs: { id: View; label: string }[] = [{ id: "overview", label: "Обзор" }, { id: "activity", label: "Активность" }, { id: "profiles", label: "Профили" }, { id: "sessions", label: "Сессии" }];
type Snapshot = { key: string; overview: BusinessOverview; profiles?: { items: ProfileAnalytics[]; total: number }; sessions?: { items: SessionAnalytics[]; total: number } };
function adminRequest<T>(promise: Promise<T>): Promise<T> {
  // Normalize before grouping so a simultaneous 503 cannot hide a revoked role.
  return promise.catch(reason => {
    if (reason instanceof ApiError && reason.status === 403) throw new ApiError(401, "administrator access changed");
    throw reason;
  });
}
function Help({ children }: { children: ReactNode }) {
  return <details className="analytics-help"><summary>Как считается</summary><div className="muted">{children}</div></details>;
}
function Facts({ items }: { items: [string, string][] }) {
  return <dl className="analytics-facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
function Chart({ data: daily, start, metric, onMetric }: { data: BusinessOverview["daily"]; start: string | null; metric: ChartMetric; onMetric: (metric: ChartMetric) => void }) {
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const data = start ? daily.filter(d => d.day >= start.slice(0, 10)) : daily;
  const observed = data.filter(d => d.observed);
  const values = data.map(d => metric === "traffic" ? d.bytes_received + d.bytes_sent : d[metric]);
  const maximum = Math.max(1, ...data.map((d, i) => d.observed ? values[i] : 0));
  const x = (index: number) => 85 + index * 595 / Math.max(1, data.length - 1);
  const y = (value: number) => 155 - 130 * value / maximum;
  const labels = { users: "Пользователи", profiles: "Профили", sessions_started: "Начатые сессии", traffic: "Трафик" };
  const format = (value: number) => metric === "traffic" ? bytes(value) : number(value);
  const segments: string[][] = [[]];
  data.forEach((d, i) => { if (d.observed) segments[segments.length - 1].push(`${x(i)},${y(values[i])}`); else if (segments[segments.length - 1].length) segments.push([]); });
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
function Pagination({ offset, total, change, label }: { offset: number; total: number; change: (offset: number) => void; label: string }) {
  return <div className="analytics-pagination"><span>{total ? `${offset + 1}–${Math.min(offset + 50, total)} из ${total}` : "Нет записей"}</span><div className="actions"><button className="secondary" aria-label={`Предыдущие ${label}`} disabled={offset === 0} onClick={() => change(Math.max(0, offset - 50))}>Назад</button><button className="secondary" aria-label={`Следующие ${label}`} disabled={offset + 50 >= total} onClick={() => change(offset + 50)}>Далее</button></div></div>;
}
export function Analytics({ onLogout }: { onLogout: () => void }) {
  const [view, setView] = useState<View>("overview");
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState<ProfileMode | "">("");
  const [onlineOnly, setOnlineOnly] = useState(false);
  const [profileOffset, setProfileOffset] = useState(0);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [selected, setSelected] = useState<{ id: string; name: string; from: "overview" | "profiles" } | null>(null);
  const [chartMetric, setChartMetric] = useState<ChartMetric>("users");
  const [failureKey, setFailureKey] = useState("");
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const query = new URLSearchParams({ days: String(view === "overview" ? 30 : days) }); if (mode) query.set("mode", mode);
  const overviewQuery = query.toString();
  const detailQuery = view === "overview" ? `${query}&online_only=true&limit=8&offset=0` : view === "profiles" ? `${query}&online_only=${onlineOnly}&limit=50&offset=${profileOffset}` : view === "sessions" ? `${query}&limit=50&offset=${sessionOffset}${selected ? `&profile_id=${selected.id}` : ""}` : "";
  const key = `${view}:${overviewQuery}:${detailQuery}`;
  const load = useMemo(() => async (signal: AbortSignal): Promise<Snapshot> => {
    try {
      if (view === "overview" || view === "profiles") {
        const [overview, profiles] = await together([adminRequest(analyticsApi.overview(overviewQuery, signal)), adminRequest(analyticsApi.profiles(detailQuery, signal))]);
        return { key, overview, profiles };
      }
      if (view === "sessions") {
        const [overview, sessions] = await together([adminRequest(analyticsApi.overview(overviewQuery, signal)), adminRequest(analyticsApi.sessions(detailQuery, signal))]);
        return { key, overview, sessions };
      }
      return { key, overview: await adminRequest(analyticsApi.overview(overviewQuery, signal)) };
    } catch (reason) {
      if (!signal.aborted) setFailureKey(key);
      throw reason;
    }
  }, [view, key, overviewQuery, detailQuery]);
  const resource = useResource(load, onLogout);
  const snapshot = resource.data?.key === key ? resource.data : undefined;
  const data = snapshot?.overview;
  const error = failureKey === key ? resource.error : "";
  function reset() { setProfileOffset(0); setSessionOffset(0); setSelected(null); }
  function openSessions(profile: ProfileAnalytics, from: "overview" | "profiles") { setSelected({ id: profile.profile_id, name: profile.device_name, from }); setSessionOffset(0); setView("sessions"); }
  return <div className="analytics-page">
    <header className="page-heading"><div><p className="eyebrow">ИСПОЛЬЗОВАНИЕ VPN</p><h1>Аналитика</h1><p className="muted">Подключения сейчас и использование VPN.</p></div><button className="ghost" onClick={() => void resource.refresh()}>Обновить</button></header>
    <div className="analytics-toolbar"><label>Режим<select aria-label="Режим аналитики" value={mode} onChange={e => { setMode(e.target.value as typeof mode); reset(); }}><option value="">Все режимы</option>{Object.entries(modeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {view !== "overview" && <label>Период<select aria-label="Период аналитики" value={days} onChange={e => { setDays(Number(e.target.value)); reset(); }}>{[7, 30, 90, 365].map(d => <option key={d} value={d}>{d} дней</option>)}</select></label>}
      <span className="analytics-freshness muted" role="status">{data ? `${error ? "Снимок устарел" : data.online.complete ? "Свежие данные" : "Данные неполные"} · обновлено ${date(data.generated_at)}` : "Ожидаем свежие данные"}<small>Обновление каждые 5 секунд</small></span>
    </div>
    {data && <details className="analytics-sources"><summary>Источники <span className="muted">{data.sources.filter(s => s.state === "fresh").length} из {data.sources.length} доступны</span></summary><div className="analytics-source-list">{data.sources.map(source => <div key={source.mode}><strong>{modeNames[source.mode]}</strong><span>{sourceNames[source.state]}</span><small className="muted">Наблюдение: {date(source.observed_at)} · соединения: {number(source.connections)} · пропуски: {number(source.capture_errors)}</small></div>)}</div><p className="muted">Сбор наблюдений: {data.collection_started_at ? date(data.collection_started_at) : "ещё не начат"}. Без сопоставленного профиля: {data.online.unmatched_connections}. Профилей без известного владельца: {data.online.unassigned_profiles}.</p></details>}
    {error && <div role="alert" className="banner error">{error} {snapshot ? "Показан устаревший снимок." : "Свежие данные недоступны."}</div>}
    {data && !data.online.complete && <div role="status" className="banner">Онлайн-данные неполные. Подтверждено соединений: {number(data.online.confirmed_connections)}. Недоступные источники не считаются пустыми.</div>}
    {data?.sources.some(s => s.capture_errors > 0) && <p role="alert" className="banner error">Зарегистрированы пропуски сбора. История сессий может быть неполной.</p>}
    <div className="analytics-tabs" role="tablist" aria-label="Разделы аналитики">{tabs.map((tab, i) => <button key={tab.id} ref={button => { tabButtons.current[i] = button; }} id={`analytics-tab-${tab.id}`} type="button" role="tab" aria-selected={view === tab.id} aria-controls="analytics-content" tabIndex={view === tab.id ? 0 : -1} onClick={() => setView(tab.id)} onKeyDown={e => { let next: number; if (e.key === "ArrowRight") next = (i + 1) % tabs.length; else if (e.key === "ArrowLeft") next = (i + tabs.length - 1) % tabs.length; else if (e.key === "Home") next = 0; else if (e.key === "End") next = tabs.length - 1; else return; e.preventDefault(); setView(tabs[next].id); tabButtons.current[next]?.focus(); }}>{tab.label}</button>)}</div>
    <div id="analytics-content" role="tabpanel" aria-labelledby={`analytics-tab-${view}`} className="analytics-content" tabIndex={0}>
    {!snapshot && !error && <p role="status">Загружаем аналитику…</p>}
    {data && snapshot && <>
      {view === "overview" && snapshot.profiles && <>
        <div className="analytics-metrics"><Metric title="Соединения сейчас" value={number(data.online.connections)} /><Metric title="Профили онлайн" value={number(data.online.profiles)} /><Metric title="Владельцы онлайн" value={number(data.online.users)} /></div>
        <section className="analytics-panel"><div className="section-heading"><h2>Подключённые профили</h2><button className="secondary" onClick={() => { setOnlineOnly(true); setProfileOffset(0); setView("profiles"); }}>Все подключённые</button></div>
          {snapshot.profiles.items.length ? <><div className="analytics-table analytics-online-table"><table><thead><tr><th>Профиль</th><th>Владелец</th><th>Режим</th><th>История</th></tr></thead><tbody>{snapshot.profiles.items.slice(0, 8).map(p => <tr key={p.profile_id}><td><strong>{p.device_name}</strong></td><td>{p.owner_email ?? "Без владельца"}</td><td>{modeNames[p.mode]}</td><td><button className="secondary compact" aria-label={`Сессии профиля ${p.device_name}`} onClick={() => openSessions(p, "overview")}>Сессии</button></td></tr>)}</tbody></table></div><p className="muted analytics-table-note">Показано {Math.min(8, snapshot.profiles.items.length)} из {snapshot.profiles.total} подключённых профилей.</p></> : <p className="empty-state">{data.online.complete ? "Подключённых профилей сейчас нет." : "Подтверждённых подключённых профилей нет. Данные источников неполные."}</p>}
          <Help>Один профиль может иметь несколько соединений. Владелец для показателя онлайн определяется на момент подключения; в списке показан текущий владелец профиля.</Help>
        </section>
      </>}
      {view === "activity" && <>
        <div className="analytics-metrics"><Metric title="Пользователи за день" value={number(data.activity.dau)} /><Metric title="За 7 дней" value={number(data.activity.wau)} /><Metric title="За 30 дней" value={number(data.activity.mau)} /></div>
        <Chart data={data.daily} start={data.collection_started_at} metric={chartMetric} onMetric={setChartMetric} />
        <section className="analytics-panel"><h2>Активность и сессии за период</h2><Facts items={[
          ["Активные владельцы", number(data.activity.users)], ["Использованные профили", number(data.activity.profiles)],
          ["Новые / возвращающиеся владельцы", `${number(data.activity.new_users)} / ${number(data.activity.returning_users)}`],
          ["Начатые / завершённые сессии", `${number(data.activity.sessions_started)} / ${number(data.activity.sessions_closed)}`],
          ["Средняя завершённая сессия", duration(data.activity.average_duration_seconds)],
          ["Переподключения / прерывания", `${number(data.activity.reconnects)} / ${number(data.activity.interruptions)}`],
        ]} /><Help>«Новый» означает первое наблюдавшееся подключение владельца. Переподключение — новая сессия профиля в течение двух минут после завершения предыдущей. Средняя длительность учитывает только подтверждённые завершения.</Help></section>
        <details className="analytics-panel analytics-details"><summary>Трафик за период</summary><Facts items={[["От клиентов к VPN-серверу", bytes(data.activity.bytes_received)], ["От VPN-сервера к клиентам", bytes(data.activity.bytes_sent)]]} /><div className="analytics-table"><table><thead><tr><th>Режим</th><th>Владельцы / профили / сессии</th><th>К серверу</th><th>К клиентам</th></tr></thead><tbody>{data.modes.map(m => <tr key={m.mode}><td>{modeNames[m.mode]}</td><td>{m.users} / {m.profiles} / {m.sessions_started}</td><td>{bytes(m.bytes_received)}</td><td>{bytes(m.bytes_sent)}</td></tr>)}</tbody></table></div><Help>В multihop учитывается клиентский вход; транзитный туннель повторно не считается.</Help></details>
        <details className="analytics-panel analytics-details"><summary>Первое подключение</summary>{data.funnel.issued === 0 && <p className="empty-state">Новых профилей для воронки пока нет.</p>}<Facts items={[
          ["Выдано → скачивание → подключение", `${data.funnel.issued} → ${data.funnel.download_recorded} → ${data.funnel.connected}`],
          ["Подключились за 7 дней", data.funnel.activation_7d_percent === null ? "—" : `${data.funnel.activation_7d_percent.toFixed(1)}%`],
          ["Подключились / завершённое окно 7 дней", `${data.funnel.connected_within_7d} / ${data.funnel.mature_7d}`],
          ["Окно 7 дней ещё не завершено", number(data.funnel.pending_7d)], ["Среднее время до подключения", duration(data.funnel.average_first_connection_seconds)],
        ]} /><Help>Воронка учитывает новые профили, выданные после начала наблюдений для соответствующего режима. Скачивание означает успешную выдачу файла сервером. Импортированные профили исключены из воронки.</Help></details>
      </>}
      {view === "profiles" && snapshot.profiles && <>
        <section className="analytics-panel"><h2>Использование доступа</h2><Facts items={[["Действующие профили", number(data.lifecycle.valid_profiles)], ["Подключений не наблюдалось", number(data.lifecycle.never_observed)], ["Не использовались 30 дней", number(data.lifecycle.inactive_30d)], ["Используются и истекают за 7 дней", number(data.lifecycle.used_expiring_7d)]]} /><Help>Отсутствие наблюдений не доказывает, что профиль никогда не использовался до начала сбора.</Help></section>
        <section className="analytics-panel"><div className="section-heading"><h2>Использование профилей</h2><label><input aria-label="Только подключённые профили" type="checkbox" checked={onlineOnly} onChange={e => { setOnlineOnly(e.target.checked); setProfileOffset(0); }} /> Только онлайн</label></div><div className="analytics-table"><table><thead><tr><th>Профиль / владелец</th><th>Подключение</th><th>Первое / последнее подключение</th><th>Сессии / трафик за период</th><th>История</th></tr></thead><tbody>{snapshot.profiles.items.map(p => <tr key={p.profile_id}><td><strong>{p.device_name}</strong><small>{p.owner_email ?? "Без владельца"} · {modeNames[p.mode]}</small></td><td>{p.online === null ? "Нет свежих данных" : p.online ? "Онлайн" : "Не подключён"}<small>Статус доступа: {p.status}</small></td><td>{date(p.first_connected_at)}<small>{date(p.last_connected_at)}</small></td><td>{p.sessions_started}<small>{bytes(p.bytes_received)} к серверу / {bytes(p.bytes_sent)} к клиентам</small></td><td><button className="secondary compact" aria-label={`Сессии профиля ${p.device_name}`} onClick={() => { openSessions(p, "profiles"); }}>Сессии</button></td></tr>)}</tbody></table></div>{!snapshot.profiles.items.length && <p className="empty-state">Профилей по выбранным условиям нет.</p>}<Pagination offset={profileOffset} total={snapshot.profiles.total} change={setProfileOffset} label="профили" /></section>
      </>}
      {view === "sessions" && snapshot.sessions && <>
        {selected && <div className="analytics-selection"><span>Профиль: <strong>{selected.name}</strong></span><div className="actions"><button className="secondary compact" onClick={() => setView(selected.from)}>{selected.from === "profiles" ? "К профилям" : "К обзору"}</button><button className="ghost compact" onClick={() => { setSelected(null); setSessionOffset(0); }}>Все сессии</button></div></div>}
        <section className="analytics-panel"><div className="section-heading"><h2>История сессий</h2></div><p className="muted">Подробная история — {data.detail_retention_days} дней, агрегаты — {data.aggregate_retention_days} дней. Время окончания прерванной сессии неизвестно.</p><div className="analytics-table"><table><thead><tr><th>Профиль / владелец</th><th>Состояние</th><th>Начало / окончание</th><th>Длительность</th><th>Трафик</th></tr></thead><tbody>{snapshot.sessions.items.map(s => <tr key={s.session_id}><td><strong>{s.device_name ?? "Неизвестный профиль"}</strong><small>{s.owner_email ?? "Владелец неизвестен"} · {modeNames[s.mode]}</small></td><td>{s.state === "closed" ? "Завершена" : s.state === "interrupted" ? "Прервана" : s.online ? "Онлайн" : "Завершение не подтверждено"}{s.recovered && <small>Восстановлена из снимка</small>}{s.reconnect && <small>Переподключение</small>}</td><td>{date(s.started_at)}<small>{s.ended_at ? date(s.ended_at) : "Окончание неизвестно"}</small></td><td>{s.duration_seconds === null ? `Наблюдалось ${duration(Math.max(0, (Date.parse(s.last_seen_at) - Date.parse(s.started_at)) / 1000))}` : duration(s.duration_seconds)}</td><td>{bytes(s.bytes_received)}<small>{bytes(s.bytes_sent)} к клиентам</small></td></tr>)}</tbody></table></div>{!snapshot.sessions.items.length && <p className="empty-state">Сессий за выбранный период не наблюдалось.</p>}<Pagination offset={sessionOffset} total={snapshot.sessions.total} change={setSessionOffset} label="сессии" /></section>
      </>}
    </>}
    </div>
  </div>;
}
