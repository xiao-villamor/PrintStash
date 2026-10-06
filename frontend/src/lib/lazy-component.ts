import { lazy } from "react";

export function lazyImport<T extends Awaited<ReturnType<Parameters<typeof lazy>[0]>>["default"]>(
  factory: () => Promise<{ default: T }>,
) {
  const key = "chunk-reload";
  return lazy<T>(() =>
    factory()
      .then((mod) => {
        try {
          sessionStorage.removeItem(key);
        } catch {
          // A successful chunk remains usable without optional retry storage.
        }
        return mod;
      })
      .catch((err) => {
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
