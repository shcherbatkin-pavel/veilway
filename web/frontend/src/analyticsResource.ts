import { analyticsApi, ApiError, together } from "./api";
import type { BusinessOverview, ProfileAnalytics, ProfileMode, SessionAnalytics } from "./api";

export type AnalyticsView = "overview" | "activity" | "profiles" | "sessions";
export type AnalyticsSnapshot = {
  key: string;
  overview: BusinessOverview;
  profiles?: { items: ProfileAnalytics[]; total: number };
  sessions?: { items: SessionAnalytics[]; total: number };
};
type AnalyticsFilters = {
  view: AnalyticsView;
  days: number;
  mode: ProfileMode | "";
  onlineOnly: boolean;
  profileOffset: number;
  sessionOffset: number;
  selected: { id: string } | null;
};
type AnalyticsRequest = {
  view: AnalyticsView;
  key: string;
  overviewQuery: string;
  detailQuery: string;
};

export function buildAnalyticsRequest({ view, days, mode, onlineOnly, profileOffset, sessionOffset, selected }: AnalyticsFilters): AnalyticsRequest {
  const query = new URLSearchParams({ days: String(view === "overview" ? 30 : days) });
  if (mode) query.set("mode", mode);
  const overviewQuery = query.toString();
  const detailQuery = view === "overview" ? `${query}&online_only=true&limit=8&offset=0`
    : view === "profiles" ? `${query}&online_only=${onlineOnly}&limit=50&offset=${profileOffset}`
    : view === "sessions" ? `${query}&limit=50&offset=${sessionOffset}${selected ? `&profile_id=${selected.id}` : ""}` : "";
  return { view, key: `${view}:${overviewQuery}:${detailQuery}`, overviewQuery, detailQuery };
}

function adminRequest<T>(promise: Promise<T>): Promise<T> {
  // Normalize before grouping so a simultaneous 503 cannot hide a revoked role.
  return promise.catch(reason => {
    if (reason instanceof ApiError && reason.status === 403) throw new ApiError(401, "administrator access changed");
    throw reason;
  });
}

export async function loadAnalytics({ view, key, overviewQuery, detailQuery }: AnalyticsRequest, signal: AbortSignal): Promise<AnalyticsSnapshot> {
  if (view === "overview" || view === "profiles") {
    const [overview, profiles] = await together([
      adminRequest(analyticsApi.overview(overviewQuery, signal)),
      adminRequest(analyticsApi.profiles(detailQuery, signal)),
    ]);
    return { key, overview, profiles };
  }
  if (view === "sessions") {
    const [overview, sessions] = await together([
      adminRequest(analyticsApi.overview(overviewQuery, signal)),
      adminRequest(analyticsApi.sessions(detailQuery, signal)),
    ]);
    return { key, overview, sessions };
  }
  return { key, overview: await adminRequest(analyticsApi.overview(overviewQuery, signal)) };
}
