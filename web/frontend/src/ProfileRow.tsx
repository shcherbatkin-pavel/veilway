import { Profile, ProfileJob } from "./api";
import { dateLabel, effectiveStatus, jobError, modes, statuses } from "./profilePresentation";
import { ProfileAction } from "./ProfileActionDialog";

interface Props {
  profile: Profile;
  job?: ProfileJob;
  admin: boolean;
  ownerLabel: string;
  busy: string;
  hasUsers: boolean;
  onDownload: (profile: Profile) => Promise<void>;
  onAction: (kind: ProfileAction["kind"], profile: Profile) => void;
}

export function ProfileRow({ profile, job, admin, ownerLabel, busy, hasUsers, onDownload, onAction }: Props) {
  const state = effectiveStatus(profile);
  const problem = jobError(job, admin);
  return (
    <article className="profile-row" aria-label={profile.device_name}>
      <div className="device">
        <span className="device-icon" aria-hidden="true">▣</span>
        <div><strong>{profile.device_name}</strong><span className="muted">Создан {dateLabel(profile.created_at)}</span></div>
      </div>
      <div data-label="Режим">
        <span className="mode-label">{modes[profile.mode]}</span>
        {profile.mode === "yc-aws-multihop" && <small>Два узла</small>}
      </div>
      {admin && <div className="owner" data-label="Владелец">{ownerLabel}</div>}
      <div data-label="Статус"><span className={`status-pill ${state}`}><i />{statuses[state]}</span></div>
      <div data-label="Действует до"><time dateTime={profile.expires_at}>{dateLabel(profile.expires_at)}</time></div>
      <div className="row-actions">
        <button className="secondary compact" disabled={state !== "active" || !!busy} onClick={() => void onDownload(profile)}>
          {busy === profile.id ? "Подождите…" : "↓ Скачать"}
        </button>
        {admin && <>
          <button className="icon-button" aria-label={`Переименовать ${profile.device_name}`} disabled={!!busy}
            onClick={() => onAction("rename", profile)}>✎</button>
          {!profile.owner_id && <button className="icon-button" aria-label={`Назначить владельца ${profile.device_name}`}
            disabled={!!busy || !hasUsers} onClick={() => onAction("assign", profile)}>＋</button>}
          {state === "active" && <button className="icon-button revoke"
            aria-label={`Отозвать ${profile.device_name}`} disabled={!!busy}
            onClick={() => onAction("revoke", profile)}>⊘</button>}
        </>}
      </div>
      {(problem || state === "revoking") && <p className={`profile-note ${problem ? "error" : "muted"}`}>
        {problem || "Ожидаем подтверждения VPN-узла. Новые скачивания отключены."}
      </p>}
    </article>
  );
}
