import { ApiError, fetchResponse, listAll, request } from "./http";

export { ApiError } from "./http";

export type VmSlug = "aws-direct" | "yc-direct";

export interface Session {
  user_id: string;
  email: string;
  role: "ADMIN" | "USER";
  csrf_token: string;
}

export interface VpnVm {
  slug: VmSlug;
  provider: "aws" | "yandex";
  state: "healthy" | "degraded" | "unknown" | "restarting";
  last_heartbeat_at: string | null;
}

export interface RestartTarget {
  slug: VmSlug;
  position: number;
  status: string;
  dispatched_at: string | null;
  recovered_at: string | null;
  error_code: string | null;
}

export interface RestartJob {
  id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  targets: RestartTarget[];
}

export async function together<T extends unknown[]>(promises: { [K in keyof T]: Promise<T[K]> }): Promise<T> {
  // Settle the whole group before polling again, and never hide an expired session.
  const results = await Promise.allSettled(promises);
  const rejected = results.filter(item => item.status === "rejected");
  const unauthorized = rejected.find(item => item.reason instanceof ApiError && item.reason.status === 401);
  if (unauthorized) throw unauthorized.reason;
  if (rejected.length) throw rejected[0].reason;
  return results.map(item => (item as PromiseFulfilledResult<unknown>).value) as T;
}

export const api = {
  session: () => request<Session>("/api/v1/auth/session"),
  approveMobile: (user_code: string, csrf: string) => request<void>("/api/v1/mobile/auth/approve", { method: "POST", body: JSON.stringify({ user_code }) }, csrf),
  logout: (csrf: string) =>
    request<void>("/api/v1/auth/logout", { method: "POST" }, csrf),
  vms: (signal?: AbortSignal) => request<VpnVm[]>("/api/v1/vpn-vms", { signal }),
  jobs: (signal?: AbortSignal) => request<RestartJob[]>("/api/v1/restart-jobs", { signal }),
  createJob: (targets: VmSlug[], csrf: string) =>
    request<RestartJob>(
      "/api/v1/restart-jobs",
      { method: "POST", body: JSON.stringify({ targets }) },
      csrf,
    ),
};

export type ProfileMode = "yc-direct" | "aws-direct" | "yc-aws-multihop";
export type ProfileStatus = "issuing" | "active" | "expired" | "revoking" | "revoked" | "failed";
export interface Profile {
  id: string; device_name: string; mode: ProfileMode; owner_id: string | null;
  status: ProfileStatus; created_at: string; expires_at: string;
}
export interface ProfileJob {
  id: string; profile_id: string; kind: "issue" | "revoke";
  status: "queued" | "running" | "succeeded" | "failed" | "needs_review";
  created_at: string; error_code: string | null;
}
export interface RegisteredUser { id: string; email: string }
export interface ProfileAudit { id: string; actor_id: string; object_id: string; action: string; result: string; created_at: string }
export interface CrlDelivery {
  version: number | null; next_update: string | null; publisher_error: string | null;
  nodes: { slug: VmSlug; status: string; acknowledged_version: number | null; last_contact_at: string | null; acknowledged_at: string | null; error_code: string | null }[];
}
export interface CreateProfile {
  idempotency_key: string; device_name: string; mode: ProfileMode;
  owner_id?: string; duration_days: number;
}

export const profilesApi = {
  profiles: (signal?: AbortSignal) => listAll<Profile>("/api/v1/profiles", signal),
  jobs: (signal?: AbortSignal) => listAll<ProfileJob>("/api/v1/profile-jobs", signal),
  users: (signal?: AbortSignal) => listAll<RegisteredUser>("/api/v1/users", signal),
  audit: (signal?: AbortSignal) => listAll<ProfileAudit>("/api/v1/profile-audit-events", signal),
  crl: (signal?: AbortSignal) => request<CrlDelivery>("/api/v1/crl-delivery", { signal }),
  create: (data: CreateProfile, csrf: string) => request<{ profile: Profile; job: ProfileJob }>("/api/v1/profiles", { method: "POST", body: JSON.stringify(data) }, csrf),
  rename: (id: string, device_name: string, csrf: string) => request<Profile>(`/api/v1/profiles/${id}`, { method: "PATCH", body: JSON.stringify({ device_name }) }, csrf),
  assign: (id: string, owner_id: string, csrf: string) => request<Profile>(`/api/v1/profiles/${id}/owner`, { method: "POST", body: JSON.stringify({ owner_id }) }, csrf),
  revoke: (id: string, key: string, csrf: string) => request<ProfileJob>(`/api/v1/profiles/${id}/revoke`, { method: "POST", body: JSON.stringify({ idempotency_key: key }) }, csrf),
  async download(id: string, csrf: string) {
    const response = await fetchResponse(`/api/v1/profiles/${id}/download`, { method: "POST", headers: { "X-CSRF-Token": csrf } });
    if (!response.ok) throw new ApiError(response.status, "download failed");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = `veilway-${id}.ovpn`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },
};

export function errorMessage(reason: unknown, download = false): string {
  if (!(reason instanceof ApiError)) return "Нет связи с панелью. Проверьте подключение и попробуйте снова.";
  if (reason.status === 503) return download ? "Скачивание временно недоступно: сервис выпуска профилей не отвечает. Попробуйте позже." : "Сервис временно недоступен. Попробуйте позже.";
  if (reason.status === 403) return "Недостаточно прав или сессия устарела. Войдите снова.";
  if (reason.status === 404) return "Профиль недоступен. Обновите список.";
  if (reason.status === 409) return "Состояние изменилось. Обновите список и проверьте доступность действия.";
  if (reason.status === 422) return "Проверьте название, владельца и срок действия профиля.";
  return "Не удалось выполнить действие. Попробуйте снова.";
}

export type Assessment = "ok" | "attention" | "unknown";
export interface NodeHealth {
  slug: VmSlug; state: VpnVm["state"]; assessment: Assessment; reasons: string[];
  last_heartbeat_at: string | null; heartbeat_age_seconds: number | null;
  uptime_seconds: number | null;
  containers: Record<string, "healthy" | "starting" | "unhealthy" | "missing"> | null;
}
export interface WorkerHealth {
  name: string; assessment: Assessment; state: "starting" | "running" | "stopped" | "stopping";
  started_at: string | null; completed_at: string | null; succeeded_at: string | null;
  in_progress: boolean; error_code: string | null; slow: boolean;
}
export interface OperationHealth {
  kind: "issue" | "revoke" | "restart"; assessment: Assessment;
  queued: number; running: number; oldest_pending_age_seconds: number | null;
  delayed: number; needs_review: number; failed_last_day: number; pki_errors: number;
  awaiting_delivery: number;
}
export interface HealthOverview {
  generated_at: string; assessment: Assessment; nodes: NodeHealth[]; workers: WorkerHealth[];
  crl: CrlDelivery & { assessment: Assessment; attempted_at: string | null;
    observation_stale: boolean; publication_expired: boolean };
  operations: OperationHealth[];
}
export const observabilityApi = {
  overview: (signal: AbortSignal) => request<HealthOverview>("/api/v1/observability/overview", { signal }),
};

export interface AnalyticsSource {
  mode: ProfileMode; state: "unconfigured" | "unavailable" | "stale" | "fresh";
  configured_at: string | null; collection_started_at: string | null;
  observed_at: string | null; age_seconds: number | null; connections: number | null; capture_errors: number;
}
export interface BusinessOverview {
  generated_at: string; period_days: number; timezone: string; collection_started_at: string | null;
  detail_retention_days: number; aggregate_retention_days: number; sources: AnalyticsSource[];
  online: { complete: boolean; connections: number | null; profiles: number | null; users: number | null;
    confirmed_connections: number; confirmed_profiles: number; confirmed_users: number;
    unmatched_connections: number; unassigned_profiles: number };
  activity: { users: number; profiles: number; dau: number; wau: number; mau: number; new_users: number;
    returning_users: number; sessions_started: number; sessions_closed: number; interruptions: number;
    reconnects: number; average_duration_seconds: number | null; bytes_received: number; bytes_sent: number };
  funnel: { issued: number; download_recorded: number; connected: number; mature_7d: number; connected_within_7d: number;
    pending_7d: number; activation_7d_percent: number | null; average_first_connection_seconds: number | null };
  lifecycle: { valid_profiles: number; never_observed: number; inactive_30d: number; used_expiring_7d: number };
  daily: { day: string; observed: boolean; users: number; profiles: number; sessions_started: number; bytes_received: number; bytes_sent: number }[];
  modes: { mode: ProfileMode; users: number; profiles: number; sessions_started: number; bytes_received: number; bytes_sent: number; connections: number | null }[];
}
export interface ProfileAnalytics {
  profile_id: string; device_name: string; mode: ProfileMode; owner_email: string | null; status: string;
  expires_at: string; online: boolean | null; first_connected_at: string | null; last_connected_at: string | null;
  last_seen_at: string | null; sessions_started: number; bytes_received: number; bytes_sent: number;
}
export interface SessionAnalytics {
  session_id: string; profile_id: string | null; device_name: string | null; owner_email: string | null;
  mode: ProfileMode; state: "open" | "closed" | "interrupted"; online: boolean | null;
  started_at: string; last_seen_at: string; ended_at: string | null; duration_seconds: number | null;
  bytes_received: number; bytes_sent: number; recovered: boolean; reconnect: boolean;
}
export const analyticsApi = {
  overview: (query: string, signal: AbortSignal) => request<BusinessOverview>(`/api/v1/analytics/overview?${query}`, { signal }),
  profiles: (query: string, signal: AbortSignal) => request<{ items: ProfileAnalytics[]; total: number }>(`/api/v1/analytics/profiles?${query}`, { signal }),
  sessions: (query: string, signal: AbortSignal) => request<{ items: SessionAnalytics[]; total: number; retained_since: string }>(`/api/v1/analytics/sessions?${query}`, { signal }),
};
