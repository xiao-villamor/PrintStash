export function createControllerChangeHandler(
  reload: () => void,
  hasController: boolean,
): () => void {
  let reloading = false;
  return () => {
    // First installation only enables offline support. Restarting the page here
    // discards startup work (and user input) without adopting a newer app version.
    if (!hasController) {
      hasController = true;
      return;
    }
    if (reloading) return;
    reloading = true;
    reload();
  };
}

export function registerPwa(enabled = import.meta.env.PROD): void {
  // Feature detection, in order: opted in, running in a DOM, service workers supported.
  if (!enabled || !("window" in globalThis) || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.addEventListener(
    "controllerchange",
    createControllerChangeHandler(
      () => window.location.reload(),
      Boolean(navigator.serviceWorker.controller),
    ),
  );
  window.addEventListener(
    "load",
    () => {
      void navigator.serviceWorker
        .register("/sw.js", { scope: "/", updateViaCache: "none" })
        .then(async (registration) => {
          if (registration.waiting) {
            registration.waiting.postMessage({ type: "SKIP_WAITING" });
          }
          await registration.update();
        })
        .catch(() => {
          // PWA support is optional; registration failure must never block app boot.
        });
    },
    { once: true },
  );
}
