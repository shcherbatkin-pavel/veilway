import { FormEvent, useCallback, useMemo, useRef, useState } from "react";
import { ApiError, errorMessage, Profile, ProfileJob, profilesApi, RegisteredUser, Session, together } from "./api";
import { ProfileRow } from "./ProfileRow";
import { ProfileCreateForm, ProfileDraft } from "./ProfileCreateForm";
import { ProfileActionDialog, ProfileAction } from "./ProfileActionDialog";
import { indexProfiles } from "./profileIndexes";
import { effectiveStatus } from "./profilePresentation";
import { ProfileFilters } from "./ProfileFilters";
import { ProfileFilterValues, profileOwnerLabel, selectProfiles } from "./profileSelection";
import { useResource } from "./useResource";

type Data = [Profile[], ProfileJob[], RegisteredUser[]];
const newDraft = (): ProfileDraft => ({ device_name: "", mode: "yc-direct", owner_id: "", days: "365", key: crypto.randomUUID() });

export function Profiles({ session, onLogout, onlyOwner }: { session: Session; onLogout: () => void; onlyOwner?: string }) {
  const admin = session.role === "ADMIN";
  const load = useCallback((signal: AbortSignal): Promise<Data> => together([
    profilesApi.profiles(signal), profilesApi.jobs(signal), admin ? profilesApi.users(signal) : Promise.resolve([]),
  ]), [admin]);
  const { data, loading, error: loadError, refresh } = useResource(load, onLogout);
  const [filters, setFilters] = useState<ProfileFilterValues>({
    query: "", status: "", mode: "", owner: onlyOwner ?? "",
  });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const lock = useRef(false);
  const [action, setAction] = useState<ProfileAction | null>(null);
  const [value, setValue] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<ProfileDraft>(() => newDraft());
  const [formError, setFormError] = useState("");
  const profiles = data?.[0] ?? [], users = data?.[2] ?? [];
  const indexes = useMemo(() => indexProfiles(data?.[0] ?? [], data?.[1] ?? [], data?.[2] ?? []), [data]);
  const ownerName = (id: string | null) => profileOwnerLabel(id, indexes.usersById);
  const { scoped, visible, counts } = selectProfiles(profiles, filters, {
    admin, onlyOwner, usersById: indexes.usersById, now: Date.now(),
  });

  function failed(reason: unknown, download = false) {
    if (reason instanceof ApiError && reason.status === 401) onLogout();
    else setError(errorMessage(reason, download));
  }
  async function download(profile: Profile) {
    if (lock.current) return;
    lock.current = true; setBusy(profile.id); setError(""); setNotice("");
    try { await profilesApi.download(profile.id, session.csrf_token); setNotice(`Скачивание «${profile.device_name}» началось.`); }
    catch (reason) { failed(reason, true); }
    finally { lock.current = false; setBusy(""); }
  }
  function openAction(kind: ProfileAction["kind"], profile: Profile) {
    setAction({ kind, profile, key: crypto.randomUUID() }); setValue(kind === "rename" ? profile.device_name : ""); setFormError("");
  }
  async function mutate(event: FormEvent) {
    event.preventDefault();
    if (!action || lock.current) return;
    lock.current = true; setBusy(action.profile.id); setFormError(""); setNotice("");
    try {
      if (action.kind === "rename") await profilesApi.rename(action.profile.id, value.trim(), session.csrf_token);
      if (action.kind === "assign") await profilesApi.assign(action.profile.id, value, session.csrf_token);
      if (action.kind === "revoke") await profilesApi.revoke(action.profile.id, action.key, session.csrf_token);
      setNotice(action.kind === "revoke" ? "Отзыв принят. Ожидаем применения на VPN-узле." : "Изменения сохранены.");
      setAction(null); await refresh();
    } catch (reason) { if (reason instanceof ApiError && reason.status === 401) onLogout(); else setFormError(errorMessage(reason)); }
    finally { lock.current = false; setBusy(""); }
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    const payload = draft.sent ?? { idempotency_key: draft.key, device_name: draft.device_name.trim(), mode: draft.mode, duration_days: Number(draft.days), ...(draft.owner_id ? { owner_id: draft.owner_id } : {}) };
    lock.current = true; setBusy("create"); setFormError(""); setDraft(current => ({ ...current, sent: payload }));
    try {
      await profilesApi.create(payload, session.csrf_token); setFormOpen(false); setDraft(newDraft());
      setNotice("Профиль выпускается. Его статус обновится автоматически."); await refresh();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onLogout();
      else {
        setFormError(errorMessage(reason));
        if (reason instanceof ApiError && [403, 422].includes(reason.status)) setDraft(current => ({ ...current, sent: undefined }));
      }
    } finally { lock.current = false; setBusy(""); }
  }
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
