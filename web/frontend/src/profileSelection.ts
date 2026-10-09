import { Profile, RegisteredUser } from "./api";
import { effectiveStatus, modes } from "./profilePresentation";

export interface ProfileFilterValues {
  query: string;
  status: string;
  mode: string;
  owner: string;
}

interface SelectionContext {
  admin: boolean;
  onlyOwner?: string;
  usersById: ReadonlyMap<string, RegisteredUser>;
  now: number;
}

export function profileOwnerLabel(id: string | null, usersById: SelectionContext["usersById"]): string {
  return id ? usersById.get(id)?.email ?? "Владелец недоступен" : "Не назначен";
}

export function selectProfiles(profiles: readonly Profile[], filters: ProfileFilterValues, context: SelectionContext) {
  const scoped: Profile[] = [];
  const visible: Profile[] = [];
  const counts = { total: 0, active: 0, pending: 0 };
  const search = filters.query.toLocaleLowerCase();

  for (const profile of profiles) {
    if (context.onlyOwner && profile.owner_id !== context.onlyOwner) continue;
    scoped.push(profile);
    counts.total++;
    const status = effectiveStatus(profile, context.now);
    if (status === "active") counts.active++;
    if (profile.status === "issuing" || profile.status === "revoking") counts.pending++;

    if (filters.status && status !== filters.status) continue;
    if (filters.mode && profile.mode !== filters.mode) continue;
    if (context.admin && filters.owner && (filters.owner === "unassigned"
      ? !!profile.owner_id : profile.owner_id !== filters.owner)) continue;
    const owner = context.admin ? profileOwnerLabel(profile.owner_id, context.usersById) : "";
    if (search && !`${profile.device_name} ${owner} ${modes[profile.mode]}`.toLocaleLowerCase().includes(search)) continue;
    visible.push(profile);
  }
  return { scoped, visible, counts };
}
