const APP_CACHE = "fish-multiplayer-v95";
const CORE_ASSETS = ["/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(APP_CACHE)
      .then((cache) => cache.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting())
      .catch(() => {})
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
      .catch(() => {})
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never cache cross-origin requests (e.g. game API calls to Render server)
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(req).catch(() => new Response("", { status: 503 })));
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    // Game state must always be live. Strip cache-control to avoid CORS preflight failures
    // when the server is on a different origin (e.g. Render vs Vercel).
    const headers = new Headers(req.headers);
    headers.delete("cache-control");
    event.respondWith(fetch(new Request(req, { headers })));
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match("/").then((r) => r || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } }))
      )
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(APP_CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() =>
          caches.match("/").then((r) => r || new Response("", { status: 503 }))
        );
    })
  );
});
