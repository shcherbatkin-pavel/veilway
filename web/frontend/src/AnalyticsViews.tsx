import type { BusinessOverview, ProfileAnalytics } from "./api";
import type { AnalyticsSnapshot } from "./analyticsResource";
import type { ChartMetric } from "./analyticsChart";
import { modeNames, number, date, bytes, duration } from "./analyticsFormatting";
import { Metric, Facts, Help, Pagination } from "./AnalyticsElements";
import { AnalyticsChart } from "./AnalyticsChart";

export type SelectedAnalyticsProfile = { id: string; name: string; from: "overview" | "profiles" };
type OpenSessions = (profile: ProfileAnalytics, from: "overview" | "profiles") => void;

export function OverviewView({ data, profiles, onOnlineOnly, openSessions }: { data: BusinessOverview; profiles: NonNullable<AnalyticsSnapshot["profiles"]>; onOnlineOnly: () => void; openSessions: OpenSessions }) {
  return <>
        <div className="analytics-metrics"><Metric title="Соединения сейчас" value={number(data.online.connections)} /><Metric title="Профили онлайн" value={number(data.online.profiles)} /><Metric title="Владельцы онлайн" value={number(data.online.users)} /></div>
        <section className="analytics-panel"><div className="section-heading"><h2>Подключённые профили</h2><button className="secondary" onClick={() => { onOnlineOnly(); }}>Все подключённые</button></div>
          {profiles.items.length ? <><div className="analytics-table analytics-online-table"><table><thead><tr><th>Профиль</th><th>Владелец</th><th>Режим</th><th>История</th></tr></thead><tbody>{profiles.items.slice(0, 8).map(p => <tr key={p.profile_id}><td><strong>{p.device_name}</strong></td><td>{p.owner_email ?? "Без владельца"}</td><td>{modeNames[p.mode]}</td><td><button className="secondary compact" aria-label={`Сессии профиля ${p.device_name}`} onClick={() => openSessions(p, "overview")}>Сессии</button></td></tr>)}</tbody></table></div><p className="muted analytics-table-note">Показано {Math.min(8, profiles.items.length)} из {profiles.total} подключённых профилей.</p></> : <p className="empty-state">{data.online.complete ? "Подключённых профилей сейчас нет." : "Подтверждённых подключённых профилей нет. Данные источников неполные."}</p>}
          <Help>Один профиль может иметь несколько соединений. Владелец для показателя онлайн определяется на момент подключения; в списке показан текущий владелец профиля.</Help>
        </section>
      </>;
}

export function ActivityView({ data, chartMetric, setChartMetric }: { data: BusinessOverview; chartMetric: ChartMetric; setChartMetric: (metric: ChartMetric) => void }) {
  return <>
        <div className="analytics-metrics"><Metric title="Пользователи за день" value={number(data.activity.dau)} /><Metric title="За 7 дней" value={number(data.activity.wau)} /><Metric title="За 30 дней" value={number(data.activity.mau)} /></div>
        <AnalyticsChart data={data.daily} start={data.collection_started_at} metric={chartMetric} onMetric={setChartMetric} />
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
      </>;
}

export function ProfilesView({ data, profiles, onlineOnly, onOnlineOnlyChange, profileOffset, setProfileOffset, openSessions }: { data: BusinessOverview; profiles: NonNullable<AnalyticsSnapshot["profiles"]>; onlineOnly: boolean; onOnlineOnlyChange: (value: boolean) => void; profileOffset: number; setProfileOffset: (offset: number) => void; openSessions: OpenSessions }) {
  return <>
        <section className="analytics-panel"><h2>Использование доступа</h2><Facts items={[["Действующие профили", number(data.lifecycle.valid_profiles)], ["Подключений не наблюдалось", number(data.lifecycle.never_observed)], ["Не использовались 30 дней", number(data.lifecycle.inactive_30d)], ["Используются и истекают за 7 дней", number(data.lifecycle.used_expiring_7d)]]} /><Help>Отсутствие наблюдений не доказывает, что профиль никогда не использовался до начала сбора.</Help></section>
        <section className="analytics-panel"><div className="section-heading"><h2>Использование профилей</h2><label><input aria-label="Только подключённые профили" type="checkbox" checked={onlineOnly} onChange={e => { onOnlineOnlyChange(e.target.checked); }} /> Только онлайн</label></div><div className="analytics-table"><table><thead><tr><th>Профиль / владелец</th><th>Подключение</th><th>Первое / последнее подключение</th><th>Сессии / трафик за период</th><th>История</th></tr></thead><tbody>{profiles.items.map(p => <tr key={p.profile_id}><td><strong>{p.device_name}</strong><small>{p.owner_email ?? "Без владельца"} · {modeNames[p.mode]}</small></td><td>{p.online === null ? "Нет свежих данных" : p.online ? "Онлайн" : "Не подключён"}<small>Статус доступа: {p.status}</small></td><td>{date(p.first_connected_at)}<small>{date(p.last_connected_at)}</small></td><td>{p.sessions_started}<small>{bytes(p.bytes_received)} к серверу / {bytes(p.bytes_sent)} к клиентам</small></td><td><button className="secondary compact" aria-label={`Сессии профиля ${p.device_name}`} onClick={() => { openSessions(p, "profiles"); }}>Сессии</button></td></tr>)}</tbody></table></div>{!profiles.items.length && <p className="empty-state">Профилей по выбранным условиям нет.</p>}<Pagination offset={profileOffset} total={profiles.total} change={setProfileOffset} label="профили" /></section>
      </>;
}

export function SessionsView({ data, sessions, selected, onBack, onClear, sessionOffset, setSessionOffset }: { data: BusinessOverview; sessions: NonNullable<AnalyticsSnapshot["sessions"]>; selected: SelectedAnalyticsProfile | null; onBack: () => void; onClear: () => void; sessionOffset: number; setSessionOffset: (offset: number) => void }) {
  return <>
        {selected && <div className="analytics-selection"><span>Профиль: <strong>{selected.name}</strong></span><div className="actions"><button className="secondary compact" onClick={onBack}>{selected.from === "profiles" ? "К профилям" : "К обзору"}</button><button className="ghost compact" onClick={onClear}>Все сессии</button></div></div>}
        <section className="analytics-panel"><div className="section-heading"><h2>История сессий</h2></div><p className="muted">Подробная история — {data.detail_retention_days} дней, агрегаты — {data.aggregate_retention_days} дней. Время окончания прерванной сессии неизвестно.</p><div className="analytics-table"><table><thead><tr><th>Профиль / владелец</th><th>Состояние</th><th>Начало / окончание</th><th>Длительность</th><th>Трафик</th></tr></thead><tbody>{sessions.items.map(s => <tr key={s.session_id}><td><strong>{s.device_name ?? "Неизвестный профиль"}</strong><small>{s.owner_email ?? "Владелец неизвестен"} · {modeNames[s.mode]}</small></td><td>{s.state === "closed" ? "Завершена" : s.state === "interrupted" ? "Прервана" : s.online ? "Онлайн" : "Завершение не подтверждено"}{s.recovered && <small>Восстановлена из снимка</small>}{s.reconnect && <small>Переподключение</small>}</td><td>{date(s.started_at)}<small>{s.ended_at ? date(s.ended_at) : "Окончание неизвестно"}</small></td><td>{s.duration_seconds === null ? `Наблюдалось ${duration(Math.max(0, (Date.parse(s.last_seen_at) - Date.parse(s.started_at)) / 1000))}` : duration(s.duration_seconds)}</td><td>{bytes(s.bytes_received)}<small>{bytes(s.bytes_sent)} к клиентам</small></td></tr>)}</tbody></table></div>{!sessions.items.length && <p className="empty-state">Сессий за выбранный период не наблюдалось.</p>}<Pagination offset={sessionOffset} total={sessions.total} change={setSessionOffset} label="сессии" /></section>
      </>;
}

