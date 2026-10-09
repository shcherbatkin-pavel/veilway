import { api, together } from "./api";
import { useResource } from "./useResource";

const load = (signal: AbortSignal) => together([api.vms(signal), api.jobs(signal)]);

export function useDashboardPolling(onLogout: () => void) {
  const { data, error, loading, refresh } = useResource(load, onLogout);
  return {
    vms: data?.[0] ?? [],
    jobs: data?.[1] ?? [],
    error: error ? "Не удалось обновить состояние" : "",
    loading,
    refresh,
  };
}
