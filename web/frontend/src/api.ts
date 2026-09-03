export type VmSlug = "aws-direct" | "yc-direct";

export interface Session {
  login: string;
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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  csrfToken?: string,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Error bodies are deliberately optional.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  login: (login: string, password: string) =>
    request<Session>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    }),
  session: () => request<Session>("/api/v1/auth/session"),
  logout: (csrf: string) =>
    request<void>("/api/v1/auth/logout", { method: "POST" }, csrf),
  vms: () => request<VpnVm[]>("/api/v1/vpn-vms"),
  jobs: () => request<RestartJob[]>("/api/v1/restart-jobs"),
  createJob: (targets: VmSlug[], csrf: string) =>
    request<RestartJob>(
      "/api/v1/restart-jobs",
      { method: "POST", body: JSON.stringify({ targets }) },
      csrf,
    ),
};

