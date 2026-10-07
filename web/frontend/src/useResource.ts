import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorMessage } from "./api";
import { createPoller } from "./polling";

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, onLogout: () => void) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const logout = useRef(onLogout); logout.current = onLogout;
  const poller = useRef<ReturnType<typeof createPoller<T>> | null>(null);
  useEffect(() => {
    const current = createPoller(load, value => { setData(value); setError(""); setLoading(false); }, reason => {
      setLoading(false);
      if (reason instanceof ApiError && reason.status === 401) logout.current();
      else setError(errorMessage(reason));
    });
    poller.current = current; current.start();
    return () => { current.stop(); poller.current = null; };
  }, [load]);
  const refresh = useCallback(() => poller.current?.refresh() ?? Promise.resolve(), []);
  return { data, error, loading, refresh };
}

export async function together<T extends unknown[]>(promises: { [K in keyof T]: Promise<T[K]> }): Promise<T> {
  const results = await Promise.allSettled(promises);
  const rejected = results.filter(item => item.status === "rejected");
  const unauthorized = rejected.find(item => item.reason instanceof ApiError && item.reason.status === 401);
  if (unauthorized) throw unauthorized.reason;
  if (rejected.length) throw rejected[0].reason;
  return results.map(item => (item as PromiseFulfilledResult<unknown>).value) as T;
}
