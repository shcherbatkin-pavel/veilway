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
