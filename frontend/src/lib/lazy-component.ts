import { lazy } from "react";

export function lazyImport<T extends Awaited<ReturnType<Parameters<typeof lazy>[0]>>["default"]>(
  factory: () => Promise<{ default: T }>,
) {
  return lazy<T>(() =>
    factory().catch((err) => {
      if (reloadForChunkFailure()) {
        return new Promise<{ default: T }>(() => {});
      }
      throw err;
    }),
  );
}

/** All deferred import paths share one tab-scoped recovery budget. */
function reloadForChunkFailure(): boolean {
  try {
    if (sessionStorage.getItem("chunk-reload")) return false;
    sessionStorage.setItem("chunk-reload", "1");
  } catch {
    // A reload without a persisted latch could repeat indefinitely.
    return false;
  }
  window.location.reload();
  return true;
}

/** Automatic route splitting adds an import boundary outside React.lazy. */
export function recoverRouteChunk(error: Error): void {
  if (
    /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Failed to load module script|Failed to load chunk/i.test(
      error.message,
    )
  )
    reloadForChunkFailure();
}
