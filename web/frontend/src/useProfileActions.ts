import { FormEvent, useRef, useState } from "react";
import { ApiError, errorMessage, Profile, profilesApi, Session } from "./api";
import type { ProfileDraft } from "./ProfileCreateForm";
import type { ProfileAction } from "./ProfileActionDialog";
import { effectiveStatus } from "./profilePresentation";

const newDraft = (): ProfileDraft => ({ device_name: "", mode: "yc-direct", owner_id: "", days: "365", key: crypto.randomUUID() });

export function useProfileActions({ session, onLogout, refresh }: {
  session: Session; onLogout: () => void; refresh: () => Promise<void>;
}) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const lock = useRef(false);
  const [action, setAction] = useState<ProfileAction | null>(null);
  const [value, setValue] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<ProfileDraft>(() => newDraft());
  const [formError, setFormError] = useState("");
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
    if (action.kind === "revoke" && effectiveStatus(action.profile) === "expired") {
      setFormError("Срок действия профиля истёк. Отзыв недоступен.");
      return;
    }
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
  return { error, notice, busy, action, value, formOpen, draft, formError,
    setAction, setValue, setFormOpen, setDraft, setFormError, download, openAction, mutate, create };
}
