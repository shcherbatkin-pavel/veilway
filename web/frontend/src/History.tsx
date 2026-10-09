import { useMemo, useState } from "react";
import { api, profilesApi, together } from "./api";
import { dateLabel } from "./profilePresentation";
import { JobHistory } from "./JobHistory";
import { useResource } from "./useResource";
import { indexProfiles } from "./profileIndexes";
const load = (signal: AbortSignal) => together([profilesApi.audit(signal), profilesApi.profiles(signal), api.jobs(signal)]);
const actions: Record<string, string> = { create: "Создание профиля", rename: "Переименование", assign: "Назначение владельца", download: "Скачивание", revoke: "Запрос отзыва", issue_result: "Выпуск профиля", revoke_result: "Применение отзыва", import: "Импорт существующего профиля" };
const outcomes: Record<string, string> = { accepted: "Принято", succeeded: "Выполнено", denied: "Отклонено", unavailable: "Сервис недоступен", failed: "Ошибка", needs_review: "Нужна проверка", local_revocation_applied: "Ожидает VPN-узел" };
export function History({ onLogout }: { onLogout: () => void }) {
  const { data, loading, error, refresh } = useResource(load, onLogout);
  const [query, setQuery] = useState(""); const [action, setAction] = useState("");
  const events = data?.[0] ?? [];
  const indexes = useMemo(() => indexProfiles(data?.[1] ?? []), [data]);
  const search = query.toLocaleLowerCase();
  const name = (id: string) => indexes.profilesById.get(id)?.device_name ?? "Профиль";
  const visible = events.filter(event => (!action || event.action === action) && `${name(event.object_id)} ${actions[event.action] ?? ""}`.toLocaleLowerCase().includes(search));
  return <><header className="page-heading"><div><p className="eyebrow">ЖУРНАЛ ДЕЙСТВИЙ</p><h1>История</h1><p className="muted">Изменения профилей и операции с VPN-узлами.</p></div><button className="ghost" onClick={() => void refresh()}>Обновить</button></header>{error && <p role="alert" className="banner error">{error}</p>}
    <section className="panel"><div className="filters"><label className="search"><span className="sr-only">Поиск в истории</span><input type="search" placeholder="Поиск профиля или действия…" value={query} onChange={event => setQuery(event.target.value)} /></label><select aria-label="Действие" value={action} onChange={event => setAction(event.target.value)}><option value="">Все действия</option>{Object.entries(actions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
      {loading ? <p role="status" className="empty-state">Загружаем историю…</p> : !data ? <p className="empty-state">История временно недоступна.</p> : !visible.length ? <p className="empty-state">Событий пока нет</p> : visible.map(event => <article className="audit-row" key={event.id}><div><strong>{actions[event.action] ?? "Действие с профилем"}</strong><span>{name(event.object_id)}</span></div><span className={`status-pill ${event.result === "succeeded" ? "active" : event.result === "failed" ? "failed" : "issuing"}`}>{outcomes[event.result] ?? "Требуется проверка"}</span><time dateTime={event.created_at}>{dateLabel(event.created_at)} · {new Date(event.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time></article>)}
    </section><JobHistory jobs={data?.[2] ?? []} />
  </>;
}
