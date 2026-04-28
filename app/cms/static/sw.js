/**
 * Service worker — CMS admin (scope /admin/).
 *
 * Mismo enfoque que el portal:
 *   - Precache del shell mínimo (login + iconos + offline).
 *   - HTML: network-first con fallback al cache.
 *   - Estáticos /admin/static/*: stale-while-revalidate.
 *   - Sin caché para POST ni para rutas que muten datos. El CMS hace
 *     navegación por server-side rendering, así que no hay /api/* propio,
 *     pero igualmente nos limitamos a GET.
 */
const CACHE_VERSION = "sprint-admin-v1";
const PRECACHE = [
  "/admin/login",
  "/admin/static/manifest.json",
  "/admin/static/icons/icon-192.png",
  "/admin/static/icons/icon-512.png",
  "/admin/static/icons/icon-maskable-512.png",
  "/admin/static/icons/apple-touch-icon.png",
  "/admin/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) =>
      Promise.all(
        PRECACHE.map((url) =>
          cache.add(url).catch((err) =>
            console.warn("[sw] precache miss", url, err.message)
          )
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith("sprint-admin-") && k !== CACHE_VERSION)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Solo nos metemos con cosas dentro de /admin/.
  if (!url.pathname.startsWith("/admin")) return;

  if (url.pathname.startsWith("/admin/static/")) {
    event.respondWith(
      caches.open(CACHE_VERSION).then((cache) =>
        cache.match(req).then((cached) => {
          const fetcher = fetch(req)
            .then((resp) => {
              if (resp && resp.status === 200) cache.put(req, resp.clone());
              return resp;
            })
            .catch(() => cached);
          return cached || fetcher;
        })
      )
    );
    return;
  }

  if (req.mode === "navigate" || req.headers.get("accept")?.includes("text/html")) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp && resp.status === 200) {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
          }
          return resp;
        })
        .catch(() =>
          caches.match(req).then(
            (cached) =>
              cached ||
              caches.match("/admin/static/offline.html") ||
              new Response("Sin conexión", { status: 503 })
          )
        )
    );
  }
});
