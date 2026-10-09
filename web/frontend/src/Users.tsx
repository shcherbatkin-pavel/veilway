import { useMemo, useState } from "react";
import { profilesApi, together } from "./api";
import { useResource } from "./useResource";
import { indexProfiles } from "./profileIndexes";
const load = (signal: AbortSignal) => together([profilesApi.users(signal), profilesApi.profiles(signal)]);
export function Users({ onLogout, onProfiles }: { onLogout: () => void; onProfiles: (owner: string) => void }) {
  const { data, loading, error, refresh } = useResource(load, onLogout);
  const [query, setQuery] = useState("");
  const users = data?.[0] ?? [];
  const indexes = useMemo(() => indexProfiles(data?.[1] ?? []), [data]);
  const search = query.toLocaleLowerCase();
  const visible = users.filter(user => user.email.toLocaleLowerCase().includes(search));
  return <><header className="page-heading"><div><p className="eyebrow">ВЛАДЕЛЬЦЫ ПРОФИЛЕЙ</p><h1>Пользователи<span className="count">{users.length}</span></h1><p className="muted">Аккаунты, зарегистрированные через Google. Доступ назначается через профили.</p></div><button className="ghost" onClick={() => void refresh()}>Обновить</button></header>{error && <p className="banner error" role="alert">{error}</p>}<section className="panel"><div className="filters"><label className="search"><span className="sr-only">Поиск пользователей</span><input type="search" placeholder="Поиск по почте…" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
    {loading ? <p className="empty-state" role="status">Загружаем пользователей…</p> : !data ? <p className="empty-state">Список временно недоступен.</p> : !visible.length ? <div className="empty-state"><h2>{query ? "Ничего не найдено" : "Пользователей пока нет"}</h2><p>Аккаунт появится после первого входа через Google.</p></div> : visible.map(user => <article className="user-row" key={user.id}><div className="avatar">{user.email.slice(0, 1).toUpperCase()}</div><div><strong>{user.email}</strong><small>Пользователь</small></div><span className="muted">Профилей: {indexes.countsByOwner.get(user.id) ?? 0}</span><button className="secondary" onClick={() => onProfiles(user.id)}>Профили пользователя →</button></article>)}
  </section></>;
}
