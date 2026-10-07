import { FormEvent, useCallback, useRef, useState } from "react";
import { ApiError, CreateProfile, errorMessage, Profile, ProfileJob, profilesApi, RegisteredUser, Session } from "./api";
import { Modal } from "./Modal";
import { dateLabel, effectiveStatus, jobError, modes, statuses } from "./profilePresentation";
import { together, useResource } from "./useResource";

type Data = [Profile[], ProfileJob[], RegisteredUser[]];
type Action = { kind: "rename" | "assign" | "revoke"; profile: Profile; key: string };
interface Draft { device_name: string; mode: CreateProfile["mode"]; owner_id: string; days: string; key: string; sent?: CreateProfile }
const newDraft = (): Draft => ({ device_name: "", mode: "yc-direct", owner_id: "", days: "365", key: crypto.randomUUID() });

export function Profiles({ session, onLogout, onlyOwner }: { session: Session; onLogout: () => void; onlyOwner?: string }) {
  const admin = session.role === "ADMIN";
  const load = useCallback((signal: AbortSignal): Promise<Data> => together([
    profilesApi.profiles(signal), profilesApi.jobs(signal), admin ? profilesApi.users(signal) : Promise.resolve([]),
  ]), [admin]);
  const { data, loading, error: loadError, refresh } = useResource(load, onLogout);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [owner, setOwner] = useState(onlyOwner ?? "");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const lock = useRef(false);
  const [action, setAction] = useState<Action | null>(null);
  const [value, setValue] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => newDraft());
  const [formError, setFormError] = useState("");
  const profiles = data?.[0] ?? [], jobs = data?.[1] ?? [], users = data?.[2] ?? [];
  const ownerName = (id: string | null) => id ? users.find(user => user.id === id)?.email ?? "Владелец недоступен" : "Не назначен";
  const visible = profiles.filter(profile => (!onlyOwner || profile.owner_id === onlyOwner)
    && (!query || `${profile.device_name} ${admin ? ownerName(profile.owner_id) : ""} ${modes[profile.mode]}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
    && (!status || effectiveStatus(profile) === status) && (!mode || profile.mode === mode)
    && (!admin || !owner || (owner === "unassigned" ? !profile.owner_id : profile.owner_id === owner)));
  const scoped = profiles.filter(profile => !onlyOwner || profile.owner_id === onlyOwner);

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
  function openAction(kind: Action["kind"], profile: Profile) {
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
  const actionTitle = action?.kind === "revoke" ? "Отозвать профиль?" : action?.kind === "assign" ? "Назначить владельца" : "Переименовать профиль";
  return <>
    <header className="page-heading"><div><p className="eyebrow">{admin ? "ДОСТУП К VPN" : "ЛИЧНЫЙ КАБИНЕТ"}</p><h1>{admin ? "Профили" : "Мои профили"}<span className="count">{scoped.length}</span></h1><p className="muted">{admin ? onlyOwner ? `Профили пользователя ${ownerName(onlyOwner)}` : "Создавайте доступ и управляйте профилями устройств." : "Ваши подключения, сроки действия и файлы настройки."}</p></div><div className="actions"><button className="ghost" onClick={() => void refresh()}>Обновить</button>{admin && <button className="primary" onClick={() => { setFormError(""); setFormOpen(true); }}>＋ Создать профиль</button>}</div></header>
    <div className="summary"><div><span>Всего профилей</span><strong>{scoped.length}</strong></div><div><span>Активные</span><strong className="accent">{scoped.filter(profile => effectiveStatus(profile) === "active").length}</strong></div><div><span>Ожидают обновления</span><strong>{scoped.filter(profile => ["issuing", "revoking"].includes(profile.status)).length}</strong></div></div>
    {(error || loadError) && <div className="banner error" role="alert">{error || loadError}{data && <span> Показаны последние полученные данные.</span>}</div>}
    {notice && <p className="banner success" role="status">{notice}</p>}
    <section className="panel">
      <div className="filters"><label className="search"><span className="sr-only">Поиск профилей</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Поиск по названию…" /></label><label><span className="sr-only">Статус</span><select aria-label="Статус" value={status} onChange={event => setStatus(event.target.value)}><option value="">Все статусы</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label><span className="sr-only">Режим VPN</span><select aria-label="Режим VPN" value={mode} onChange={event => setMode(event.target.value)}><option value="">Все режимы</option>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>{admin && !onlyOwner && <select aria-label="Владелец" value={owner} onChange={event => setOwner(event.target.value)}><option value="">Все владельцы</option><option value="unassigned">Не назначен</option>{users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}</select>}</div>
      {loading ? <div className="empty-state" role="status">Загружаем профили…</div> : !data ? <div className="empty-state">Не удалось загрузить профили.<button className="secondary" onClick={() => void refresh()}>Повторить</button></div> : scoped.length === 0 ? <div className="empty-state"><span className="empty-symbol" aria-hidden="true">▤</span><h2>{admin ? "Профилей пока нет" : "Вам ещё не назначены профили"}</h2><p>{admin ? "Создайте первый профиль и выберите его владельца." : "Администратор назначит вам VPN-профиль. Он появится здесь."}</p></div> : visible.length === 0 ? <div className="empty-state"><h2>Ничего не найдено</h2><p>Попробуйте изменить поиск или фильтры.</p></div> : <div className={`profile-list ${admin ? "admin-list" : "user-list"}`}>
        <div className="list-head"><span>Устройство</span><span>Режим</span>{admin && <span>Владелец</span>}<span>Статус</span><span>Действует до</span><span>Действия</span></div>
        {visible.map(profile => {
          const state = effectiveStatus(profile), job = jobs.find(item => item.profile_id === profile.id && item.kind === (state === "revoking" ? "revoke" : "issue"));
          const problem = jobError(job, admin);
          return <article className="profile-row" key={profile.id} aria-label={profile.device_name}>
            <div className="device"><span className="device-icon" aria-hidden="true">▣</span><div><strong>{profile.device_name}</strong><span className="muted">Создан {dateLabel(profile.created_at)}</span></div></div>
            <div data-label="Режим"><span className="mode-label">{modes[profile.mode]}</span>{profile.mode === "yc-aws-multihop" && <small>Два узла</small>}</div>
            {admin && <div className="owner" data-label="Владелец">{ownerName(profile.owner_id)}</div>}
            <div data-label="Статус"><span className={`status-pill ${state}`}><i />{statuses[state]}</span></div>
            <div data-label="Действует до"><time dateTime={profile.expires_at}>{dateLabel(profile.expires_at)}</time></div>
            <div className="row-actions"><button className="secondary compact" disabled={state !== "active" || !!busy} onClick={() => void download(profile)}>{busy === profile.id ? "Подождите…" : "↓ Скачать"}</button>{admin && <><button className="icon-button" aria-label={`Переименовать ${profile.device_name}`} disabled={!!busy} onClick={() => openAction("rename", profile)}>✎</button>{!profile.owner_id && <button className="icon-button" aria-label={`Назначить владельца ${profile.device_name}`} disabled={!!busy || !users.length} onClick={() => openAction("assign", profile)}>＋</button>}{state === "active" || state === "expired" ? <button className="icon-button revoke" aria-label={`Отозвать ${profile.device_name}`} disabled={!!busy} onClick={() => openAction("revoke", profile)}>⊘</button> : null}</>}</div>
            {(problem || state === "revoking") && <p className={`profile-note ${problem ? "error" : "muted"}`}>{problem || "Ожидаем подтверждения VPN-узла. Новые скачивания отключены."}</p>}
          </article>;
        })}
      </div>}
      {data && scoped.length > 0 && <div className="list-footer">Показано {visible.length} из {scoped.length}<span>Обновляется автоматически</span></div>}
    </section>
    {formOpen && <Modal title="Создать профиль" busy={busy === "create"} onClose={() => setFormOpen(false)}><p className="muted">Один профиль — одно устройство. Вы сможете назначить владельца позже.</p><form onSubmit={event => void create(event)}><fieldset disabled={!!draft.sent || !!busy}><label>Название устройства<input required maxLength={128} value={draft.device_name} onChange={event => setDraft(current => ({ ...current, device_name: event.target.value }))} placeholder="Например, MacBook Павла" autoFocus /></label><label>Режим VPN<select aria-label="Режим VPN" value={draft.mode} onChange={event => setDraft(current => ({ ...current, mode: event.target.value as CreateProfile["mode"] }))}>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>Владелец<select aria-label="Владелец" value={draft.owner_id} onChange={event => setDraft(current => ({ ...current, owner_id: event.target.value }))}><option value="">Назначить позже</option>{users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}</select></label><label>Срок действия, дней<input type="number" required min={1} max={36500} step={1} value={draft.days} onChange={event => setDraft(current => ({ ...current, days: event.target.value }))} /></label></fieldset>{formError && <p className="error" role="alert">{formError}</p>}{draft.sent && !busy && <p className="muted">Повтор отправит тот же запрос. Параметры сохранены, чтобы не выпустить профиль дважды.</p>}<div className="modal-actions"><button type="button" className="ghost" disabled={!!busy} onClick={() => setFormOpen(false)}>Отмена</button><button className="primary" disabled={!!busy || loading || !data}>{busy === "create" ? "Создаём…" : draft.sent ? "Повторить" : "Создать"}</button></div></form></Modal>}
    {action && <Modal title={actionTitle} busy={!!busy} onClose={() => setAction(null)}><form onSubmit={event => void mutate(event)}>{action.kind === "revoke" ? <p>Профиль «{action.profile.device_name}» больше не сможет подключаться после применения отзыва на узле. Уже активные соединения не будут завершены принудительно.</p> : action.kind === "rename" ? <label>Название устройства<input autoFocus required maxLength={128} value={value} onChange={event => setValue(event.target.value)} /></label> : <><p className="muted">Назначенного владельца нельзя изменить. Для передачи доступа другому пользователю отзовите профиль и создайте новый.</p><label>Пользователь<select aria-label="Пользователь" required value={value} onChange={event => setValue(event.target.value)}><option value="">Выберите пользователя</option>{users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}</select></label></>}{formError && <p className="error" role="alert">{formError}</p>}<div className="modal-actions"><button type="button" className="ghost" disabled={!!busy} onClick={() => setAction(null)}>Отмена</button><button className={action.kind === "revoke" ? "danger" : "primary"} disabled={!!busy}>{busy ? "Отправляем…" : action.kind === "revoke" ? "Отозвать профиль" : "Сохранить"}</button></div></form></Modal>}
  </>;
}
