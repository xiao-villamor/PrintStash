import { lazy } from "react";

export function lazyImport<T extends Awaited<ReturnType<Parameters<typeof lazy>[0]>>["default"]>(
  factory: () => Promise<{ default: T }>,
) {
  // A successful unrelated chunk must not renew the tab's automatic retry budget.
  const key = "chunk-reload";
  return lazy<T>(() =>
    factory().catch((err) => {
      let canReload = false;
      try {
        if (!sessionStorage.getItem(key)) {
          sessionStorage.setItem(key, "1");
          canReload = true;
        }
      } catch {
        // Without a persisted latch, a reload could repeat indefinitely.
      }
      if (canReload) {
        window.location.reload();
        return new Promise<{ default: T }>(() => {});
      }
      throw err;
    }),
  );
}
