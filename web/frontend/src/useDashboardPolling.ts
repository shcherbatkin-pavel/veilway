import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, RestartJob, VpnVm } from "./api";
import { createPoller } from "./polling";

export function useDashboardPolling(onLogout: () => void) {
  const [vms, setVms] = useState<VpnVm[]>([]);
  const [jobs, setJobs] = useState<RestartJob[]>([]);
  const [error, setError] = useState("");
  const logout = useRef(onLogout);
  logout.current = onLogout;
  const poller = useRef<ReturnType<typeof createPoller<[VpnVm[], RestartJob[]]>> | null>(null);

  useEffect(() => {
    const current = createPoller(
      async (signal): Promise<[VpnVm[], RestartJob[]]> => {
        // Wait for both requests even on failure, so the next poll cannot overlap.
        const [vmResult, jobResult] = await Promise.allSettled([api.vms(signal), api.jobs(signal)]);
        if (vmResult.status === "rejected") throw vmResult.reason;
        if (jobResult.status === "rejected") throw jobResult.reason;
        return [vmResult.value, jobResult.value];
      },
      ([nextVms, nextJobs]) => {
        setVms(nextVms);
        setJobs(nextJobs);
        setError("");
      },
      (reason) => {
        if (reason instanceof ApiError && reason.status === 401) logout.current();
        else setError("Не удалось обновить состояние");
      },
    );
    poller.current = current;
    current.start();
    return () => {
      current.stop();
      poller.current = null;
    };
  }, []);

  const refresh = useCallback(() => poller.current?.refresh() ?? Promise.resolve(), []);
  return { vms, jobs, error, refresh };
}
