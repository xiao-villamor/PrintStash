import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** One sustained pointer/focus intent; cancellation owns only its speculative work. */
export function useIntentPrefetch(
  load: (path: string, signal: AbortSignal) => void,
  enabled: boolean,
) {
  const latest = useRef(load);
  useLayoutEffect(() => {
    latest.current = load;
  }, [load]);
  const [path, setPath] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled || path === null) return;
    const controller = new AbortController();
    const timer = setTimeout(() => latest.current(path, controller.signal), 150);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, enabled]);
  return useCallback((next: string | null) => setPath(next), []);
}
