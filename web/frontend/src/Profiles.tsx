import { useCallback, useMemo, useState } from "react";
import { Profile, ProfileJob, profilesApi, RegisteredUser, Session, together } from "./api";
import { ProfileRow } from "./ProfileRow";
import { ProfileCreateForm } from "./ProfileCreateForm";
import { ProfileActionDialog } from "./ProfileActionDialog";
import { indexProfiles } from "./profileIndexes";
import { effectiveStatus } from "./profilePresentation";
import { ProfileFilters } from "./ProfileFilters";
import { ProfileFilterValues, profileOwnerLabel, selectProfiles } from "./profileSelection";
import { useProfileActions } from "./useProfileActions";
import { useResource } from "./useResource";

type Data = [Profile[], ProfileJob[], RegisteredUser[]];
export function Profiles({ session, onLogout, onlyOwner }: { session: Session; onLogout: () => void; onlyOwner?: string }) {
  const admin = session.role === "ADMIN";
  const load = useCallback((signal: AbortSignal): Promise<Data> => together([
    profilesApi.profiles(signal), profilesApi.jobs(signal), admin ? profilesApi.users(signal) : Promise.resolve([]),
  ]), [admin]);
  const { data, loading, error: loadError, refresh } = useResource(load, onLogout);
  const [filters, setFilters] = useState<ProfileFilterValues>({
    query: "", status: "", mode: "", owner: onlyOwner ?? "",
  });
  const { error, notice, busy, action, value, formOpen, draft, formError,
    setAction, setValue, setFormOpen, setDraft, setFormError, download, openAction, mutate, create } =
    useProfileActions({ session, onLogout, refresh });
  const profiles = data?.[0] ?? [], users = data?.[2] ?? [];
  const indexes = useMemo(() => indexProfiles(data?.[0] ?? [], data?.[1] ?? [], data?.[2] ?? []), [data]);
  const ownerName = (id: string | null) => profileOwnerLabel(id, indexes.usersById);
  const { scoped, visible, counts } = selectProfiles(profiles, filters, {
    admin, onlyOwner, usersById: indexes.usersById, now: Date.now(),
  });

  return <>
    <header className="page-heading"><div><p className="eyebrow">{admin ? "ДОСТУП К VPN" : "ЛИЧНЫЙ КАБИНЕТ"}</p><h1>{admin ? "Профили" : "Мои профили"}<span className="count">{scoped.length}</span></h1><p className="muted">{admin ? onlyOwner ? `Профили пользователя ${ownerName(onlyOwner)}` : "Создавайте доступ и управляйте профилями устройств." : "Ваши подключения, сроки действия и файлы настройки."}</p></div><div className="actions"><button className="ghost" onClick={() => void refresh()}>Обновить</button>{admin && <button className="primary" onClick={() => { setFormError(""); setFormOpen(true); }}>＋ Создать профиль</button>}</div></header>
    <div className="summary"><div><span>Всего профилей</span><strong>{counts.total}</strong></div><div><span>Активные</span><strong className="accent">{counts.active}</strong></div><div><span>Ожидают обновления</span><strong>{counts.pending}</strong></div></div>
    {(error || loadError) && <div className="banner error" role="alert">{error || loadError}{data && <span> Показаны последние полученные данные.</span>}</div>}
    {notice && <p className="banner success" role="status">{notice}</p>}
    <section className="panel">
      <ProfileFilters filters={filters} onChange={change => setFilters(current => ({ ...current, ...change }))}
        admin={admin} onlyOwner={onlyOwner} users={users} />
      {loading ? <div className="empty-state" role="status">Загружаем профили…</div> : !data ? <div className="empty-state">Не удалось загрузить профили.<button className="secondary" onClick={() => void refresh()}>Повторить</button></div> : scoped.length === 0 ? <div className="empty-state"><span className="empty-symbol" aria-hidden="true">▤</span><h2>{admin ? "Профилей пока нет" : "Вам ещё не назначены профили"}</h2><p>{admin ? "Создайте первый профиль и выберите его владельца." : "Администратор назначит вам VPN-профиль. Он появится здесь."}</p></div> : visible.length === 0 ? <div className="empty-state"><h2>Ничего не найдено</h2><p>Попробуйте изменить поиск или фильтры.</p></div> : <div className={`profile-list ${admin ? "admin-list" : "user-list"}`}>
        <div className="list-head"><span>Устройство</span><span>Режим</span>{admin && <span>Владелец</span>}<span>Статус</span><span>Действует до</span><span>Действия</span></div>
        {visible.map(profile => <ProfileRow key={profile.id} profile={profile} admin={admin}
          ownerLabel={ownerName(profile.owner_id)} busy={busy} hasUsers={users.length > 0}
          job={indexes.jobsByProfileAndKind.get(`${profile.id}:${effectiveStatus(profile) === "revoking" ? "revoke" : "issue"}`)}
          onDownload={download} onAction={openAction} />)}
      </div>}
      {data && scoped.length > 0 && <div className="list-footer">Показано {visible.length} из {scoped.length}<span>Обновляется автоматически</span></div>}
    </section>
    {formOpen && <ProfileCreateForm draft={draft} setDraft={setDraft} users={users} busy={busy}
      unavailable={loading || !data} error={formError} onClose={() => setFormOpen(false)} onSubmit={create} />}
    {action && <ProfileActionDialog action={action} value={value} setValue={setValue} users={users}
      busy={!!busy} error={formError} onClose={() => setAction(null)} onSubmit={mutate} />}
  </>;
}
