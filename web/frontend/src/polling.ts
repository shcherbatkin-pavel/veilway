// One request group at a time; stop also suppresses late callbacks.
export function createPoller<T>(
  load: (signal: AbortSignal) => Promise<T>,
  onData: (data: T) => void,
  onError: (error: unknown) => void,
) {
  const controller = new AbortController();
  let pending: Promise<void> | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;

  function refresh(): Promise<void> {
    if (controller.signal.aborted) return Promise.resolve();
    if (pending) return pending;
    pending = Promise.resolve()
      .then(async () => {
        if (controller.signal.aborted) return;
        const data = await load(controller.signal);
        if (!controller.signal.aborted) onData(data);
      })
      .catch((error: unknown) => { if (!controller.signal.aborted) onError(error); })
      .finally(() => { pending = undefined; });
    return pending;
  }

  return {
    refresh,
    start() {
      if (timer !== undefined || controller.signal.aborted) return;
      void refresh();
      timer = setInterval(() => void refresh(), 5000);
    },
    stop() {
      controller.abort();
      clearInterval(timer);
      timer = undefined;
    },
  };
}
