import { FormEvent } from "react";
import { Profile, RegisteredUser } from "./api";
import { Modal } from "./Modal";

export interface ProfileAction {
  kind: "rename" | "assign" | "revoke";
  profile: Profile;
  key: string;
}

interface Props {
  action: ProfileAction;
  value: string;
  setValue: (value: string) => void;
  users: RegisteredUser[];
  busy: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (event: FormEvent) => Promise<void>;
}

export function ProfileActionDialog({ action, value, setValue, users, busy, error, onClose, onSubmit }: Props) {
  const title = action.kind === "revoke" ? "Отозвать профиль?" : action.kind === "assign" ? "Назначить владельца" : "Переименовать профиль";
  return (
    <Modal title={title} busy={busy} onClose={onClose}>
      <form onSubmit={event => void onSubmit(event)}>
        {action.kind === "revoke" ? (
          <p>Профиль «{action.profile.device_name}» больше не сможет подключаться после применения отзыва на узле. Уже активные соединения не будут завершены принудительно.</p>
        ) : action.kind === "rename" ? (
          <label>Название устройства
            <input autoFocus required maxLength={128} value={value} onChange={event => setValue(event.target.value)} />
          </label>
        ) : (
          <>
            <p className="muted">Назначенного владельца нельзя изменить. Для передачи доступа другому пользователю отзовите профиль и создайте новый.</p>
            <label>Пользователь
              <select aria-label="Пользователь" required value={value} onChange={event => setValue(event.target.value)}>
                <option value="">Выберите пользователя</option>
                {users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}
              </select>
            </label>
          </>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="ghost" disabled={busy} onClick={onClose}>Отмена</button>
          <button className={action.kind === "revoke" ? "danger" : "primary"} disabled={busy}>
            {busy ? "Отправляем…" : action.kind === "revoke" ? "Отозвать профиль" : "Сохранить"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
