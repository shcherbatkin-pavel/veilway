import { RegisteredUser } from "./api";
import { modes, statuses } from "./profilePresentation";
import { ProfileFilterValues } from "./profileSelection";

interface Props {
  filters: ProfileFilterValues;
  onChange: (change: Partial<ProfileFilterValues>) => void;
  admin: boolean;
  onlyOwner?: string;
  users: RegisteredUser[];
}

export function ProfileFilters({ filters, onChange, admin, onlyOwner, users }: Props) {
  return (
    <div className="filters">
      <label className="search">
        <span className="sr-only">Поиск профилей</span>
        <input type="search" value={filters.query} onChange={event => onChange({ query: event.target.value })}
          placeholder="Поиск по названию…" />
      </label>
      <label>
        <span className="sr-only">Статус</span>
        <select aria-label="Статус" value={filters.status} onChange={event => onChange({ status: event.target.value })}>
          <option value="">Все статусы</option>
          {Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label>
        <span className="sr-only">Режим VPN</span>
        <select aria-label="Режим VPN" value={filters.mode} onChange={event => onChange({ mode: event.target.value })}>
          <option value="">Все режимы</option>
          {Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      {admin && !onlyOwner && <select aria-label="Владелец" value={filters.owner}
        onChange={event => onChange({ owner: event.target.value })}>
        <option value="">Все владельцы</option>
        <option value="unassigned">Не назначен</option>
        {users.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}
      </select>}
    </div>
  );
}
