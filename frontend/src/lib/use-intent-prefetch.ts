import { useCallback, useLayoutEffect, useRef } from "react";

/** One sustained pointer/focus intent; cancellation owns only its speculative work. */
export function useIntentPrefetch(
  load: (path: string, signal: AbortSignal) => void,
  enabled: boolean,
) {
  const latest = useRef(load);
  const allowed = useRef(enabled);
  const path = useRef<string | null>(null);
  const pending = useRef<{
    timer: ReturnType<typeof setTimeout>;
    controller: AbortController;
  } | null>(null);
  const cancel = useCallback(() => {
    if (!pending.current) return;
    clearTimeout(pending.current.timer);
    pending.current.controller.abort();
    pending.current = null;
  }, []);
  const schedule = useCallback(() => {
    const target = path.current;
    if (!allowed.current || target === null) return;
    const controller = new AbortController();
    const timer = setTimeout(() => latest.current(target, controller.signal), 150);
    pending.current = { timer, controller };
  }, []);
  useLayoutEffect(() => {
    latest.current = load;
  }, [load]);
  useLayoutEffect(() => {
    allowed.current = enabled;
    schedule();
    return cancel;
  }, [enabled, schedule, cancel]);
  // Pointer movement has no visible state. Scheduling through React state made
  // hovering each folder render the entire departing grid before a click.
  return useCallback(
    (next: string | null) => {
      if (path.current === next) return;
      cancel();
      path.current = next;
      schedule();
    },
    [cancel, schedule],
  );
}
