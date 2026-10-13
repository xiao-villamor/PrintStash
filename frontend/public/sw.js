/* global self, caches */
const CACHE = "printstash-shell-v6";
const BOOTSTRAP = ["/theme-bootstrap.js", "/locale-shell.js"];
const PUBLIC_FILES = [
  "/logo.svg",
  "/logo-dark.svg",
  "/logo.png",
  "/images/printers/generic-fdm.png",
  "/manifest.en.webmanifest",
  "/manifest.es.webmanifest",
];
const SHELL = [
  "/",
  "/offline.html",
  ...BOOTSTRAP,
  "/manifest.webmanifest",
  "/icon-light.svg",
  "/icon-dark.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("printstash-shell-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

// Cache storage is best effort. An unavailable cache must not hide usable network bytes.
function cached(key) {
  return caches
    .open(CACHE)
    .then((cache) => cache.match(key))
    .catch(() => undefined);
}
function remember(key, response, immutable = false) {
  if (!response.ok) return Promise.resolve();
  const copy = response.clone();
  return caches.open(CACHE).then(async (cache) => {
    // Hashed build assets never change at this URL. Avoid rewriting the entire
    // JS graph on every warm navigation; persistence remains outside delivery.
    if (immutable && (await cache.match(key))) return;
    await cache.put(key, copy);
  });
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    request.headers.has("Authorization") ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/")
  )
    return;

  const navigation = request.mode === "navigate";
  const immutable = url.pathname.startsWith("/assets/");
  if (
    !navigation &&
    !immutable &&
    !SHELL.includes(url.pathname) &&
    !PUBLIC_FILES.includes(url.pathname)
  )
    return;
  const key = navigation ? "/" : request;
  // Reload requests normally bypass HTTP cache. Content-hashed assets are
  // immutable, so reuse that exact URL without retransferring the JS graph.
  // Keep mutable documents/bootstrap on their original revalidation policy.
  const network = fetch(request, immutable ? { cache: "force-cache" } : undefined);
  event.waitUntil(
    network.then((response) => remember(key, response, immutable)).catch(() => undefined),
  );

  if (navigation || immutable || BOOTSTRAP.includes(url.pathname)) {
    const fallback = async () =>
      (await cached(key)) || (navigation ? await cached("/offline.html") : undefined);
    event.respondWith(
      network.then(
        async (response) => (response.ok ? response : (await fallback()) || response),
        async () => (await fallback()) || Response.error(),
      ),
    );
    return;
  }
  event.respondWith(cached(request).then((response) => response || network));
});
