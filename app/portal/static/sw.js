/**
 * Service worker — Portal cliente (scope /app/).
 *
 * Estrategia:
 *   - Precache del shell mínimo: la página /app/login (sin auth), iconos y
 *     manifest. La home /app requiere sesión, así que no la precacheamos.
 *   - HTML: network-first con fallback al cache (y a la página offline si
 *     no hay nada cacheado).
 *   - Estáticos /app/static/*: stale-while-revalidate.
 *   - /api/portal/*: network-only (no cacheamos datos del cliente).
 *
 * Bump CACHE_VERSION cuando cambie la lista de precache o la lógica.
 */
const CACHE_VERSION = "sprint-portal-v1";
const PRECACHE = [
  "/app/login",
  "/app/static/manifest.json",
  "/app/static/icons/icon-192.png",
  "/app/static/icons/icon-512.png",
  "/app/static/icons/icon-maskable-512.png",
  "/app/static/icons/apple-touch-icon.png",
  "/app/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) =>
      // addAll falla todo el install si una sola URL falla; usamos add
      // individual y silenciamos errores para que el SW se instale aunque
      // falte un asset puntual.
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
          .filter((k) => k.startsWith("sprint-portal-") && k !== CACHE_VERSION)
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

  // Solo gestionamos peticiones del propio origen.
  if (url.origin !== self.location.origin) return;

  // API: nunca cachear. Si no hay red, devolvemos 503 sintético para que
  // el SPA pueda mostrar su propio mensaje en lugar de un fallo silencioso.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(req).catch(
        () =>
          new Response(
            JSON.stringify({ error: "offline", detail: "Sin conexión" }),
            {
              status: 503,
              headers: { "Content-Type": "application/json" },
            }
          )
      )
    );
    return;
  }

  // Assets estáticos del portal: stale-while-revalidate.
  if (url.pathname.startsWith("/app/static/")) {
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

  // Navegaciones HTML dentro del scope /app/: network-first con fallback.
  if (req.mode === "navigate" || req.headers.get("accept")?.includes("text/html")) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          // Cacheamos solo respuestas OK del propio scope.
          if (resp && resp.status === 200 && url.pathname.startsWith("/app")) {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(req, copy));
          }
          return resp;
        })
        .catch(() =>
          caches.match(req).then(
            (cached) =>
              cached ||
              caches.match("/app/static/offline.html") ||
              new Response("Sin conexión", { status: 503 })
          )
        )
    );
    return;
  }
});
