import { Dispatch, FormEvent, SetStateAction } from "react";
import { CreateProfile, RegisteredUser } from "./api";
import { Modal } from "./Modal";
import { modes } from "./profilePresentation";

export interface ProfileDraft {
  device_name: string;
  mode: CreateProfile["mode"];
  owner_id: string;
  days: string;
  key: string;
  sent?: CreateProfile;
}

interface Props {
  draft: ProfileDraft;
  setDraft: Dispatch<SetStateAction<ProfileDraft>>;
  users: RegisteredUser[];
  busy: string;
  unavailable: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (event: FormEvent) => Promise<void>;
}

export function ProfileCreateForm({ draft, setDraft, users, busy, unavailable, error, onClose, onSubmit }: Props) {
  return (
    <Modal title="Создать профиль" busy={busy === "create"} onClose={onClose}>
      <p className="muted">Один профиль — одно устройство. Вы сможете назначить владельца позже.</p>
      <form onSubmit={event => void onSubmit(event)}>
        <fieldset disabled={!!draft.sent || !!busy}>
          <label>Название устройства
            <input required maxLength={128} value={draft.device_name}
              onChange={event => setDraft(current => ({ ...current, device_name: event.target.value }))}
              placeholder="Например, MacBook Павла" autoFocus />
          </label>
          <label>Режим VPN
            <select aria-label="Режим VPN" value={draft.mode}
              onChange={event => setDraft(current => ({ ...current, mode: event.target.value as CreateProfile["mode"] }))}>
              {Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <label>Владелец
            <select aria-label="Владелец" value={draft.owner_id}
              onChange={event => setDraft(current => ({ ...current, owner_id: event.target.value }))}>
              <option value="">Назначить позже</option>
              {users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}
            </select>
          </label>
          <label>Срок действия, дней
            <input type="number" required min={1} max={36500} step={1} value={draft.days}
              onChange={event => setDraft(current => ({ ...current, days: event.target.value }))} />
          </label>
        </fieldset>
        {error && <p className="error" role="alert">{error}</p>}
        {draft.sent && !busy && <p className="muted">Повтор отправит тот же запрос. Параметры сохранены, чтобы не выпустить профиль дважды.</p>}
        <div className="modal-actions">
          <button type="button" className="ghost" disabled={!!busy} onClick={onClose}>Отмена</button>
          <button className="primary" disabled={!!busy || unavailable}>
            {busy === "create" ? "Создаём…" : draft.sent ? "Повторить" : "Создать"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
