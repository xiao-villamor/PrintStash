import { lazy } from "react";

export function lazyImport<T extends Awaited<ReturnType<Parameters<typeof lazy>[0]>>["default"]>(
  factory: () => Promise<{ default: T }>,
) {
  const key = "chunk-reload";
  return lazy<T>(() =>
    factory()
      .then((mod) => {
        sessionStorage.removeItem(key);
        return mod;
      })
      .catch((err) => {
        if (!sessionStorage.getItem(key)) {
          sessionStorage.setItem(key, "1");
          window.location.reload();
          return new Promise<{ default: T }>(() => {});
        }
        throw err;
      }),
  );
}
