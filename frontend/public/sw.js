/* global self, caches */
const CACHE = "printstash-shell-v3";
const SHELL = ["/", "/offline.html", "/manifest.webmanifest", "/icon-light.svg", "/icon-dark.svg"];

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
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/")
  )
    return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            void caches.open(CACHE).then((cache) => cache.put("/", copy));
          }
          return response;
        })
        .catch(async () => (await caches.match("/")) || caches.match("/offline.html")),
    );
    return;
  }

  const network = fetch(request).then((response) => {
    if (response.ok) {
      void caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
    }
    return response;
  });
  event.respondWith(caches.match(request).then((cached) => cached || network));
  event.waitUntil(network.catch(() => undefined));
});
