/*
 * Gabsweb CashFlow service worker.
 *
 * Security classification:
 * - Public static assets under /static/ may be cached (CSS, JS, fonts, icons).
 * - HTML navigations are NEVER cached. They always go to Django so session,
 *   ownership and authentication checks remain authoritative.
 * - Private financial APIs, profile routes, reports, PDFs and account pages are
 *   never cached because only /static/ responses may enter a cache. This is
 *   enforced again in `fetch` before any response is considered for caching.
 * - Non-GET requests and cross-origin requests are left to the network.
 *
 * Update strategy:
 * - Application code (JavaScript and CSS) is network-first with a cache
 *   fallback, so a deployment is picked up on the next online load.
 * - Images, fonts and other public static assets are stale-while-revalidate.
 * - The cache name carries CACHE_VERSION. Activating a new worker deletes every
 *   older `gabsweb-` cache so obsolete assets are removed.
 *
 * Bump CACHE_VERSION whenever static assets change in a way that must not be
 * served from a previous cache.
 */

const CACHE_VERSION = "2026-11-15.5";
const PRECACHE = `gabsweb-precache-${CACHE_VERSION}`;
const STATIC_CACHE = `gabsweb-static-${CACHE_VERSION}`;
const CACHE_PREFIX = "gabsweb-";
const OFFLINE_URL = "/static/assets/offline.html";

const PRECACHE_URLS = [
  OFFLINE_URL,
  "/static/assets/icons/gabsweb_PWA_Maskable_Icon.png?v=20261115d",
  "/static/assets/images/gabsweb-cashflow-logo.png?v=20261115i"
];

const CODE_ASSET_PATTERN = /\.(?:css|js)$/i;

const OFFLINE_FALLBACK_HTML =
  '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  "<title>Offline · Gabsweb CashFlow</title></head>" +
  '<body style="font-family:system-ui,sans-serif;padding:32px;text-align:center">' +
  "<h1>You are offline</h1>" +
  "<p>Gabsweb CashFlow needs a connection to load live financial information.</p>" +
  '<button type="button" onclick="window.location.reload()">Try again</button>' +
  "</body></html>";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(PRECACHE).then((cache) =>
      // Cache each URL independently: one missing optional asset must not keep
      // the whole worker from installing. The offline page has an inline
      // fallback, so offline navigation still works if precaching is partial.
      Promise.all(
        PRECACHE_URLS.map((url) =>
          cache.add(url).catch(() => undefined)
        )
      )
    ).then(() => {
      // The first install has no active worker to replace, so activation can
      // happen immediately. Updates still wait for an explicit user action.
      if (!self.registration.active) return self.skipWaiting();
      return undefined;
    })
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (key) =>
                key.startsWith(CACHE_PREFIX) &&
                key !== PRECACHE &&
                key !== STATIC_CACHE
            )
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "SKIP_WAITING") return;
  self.skipWaiting();
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/static/");
}

function isCodeAsset(url) {
  return CODE_ASSET_PATTERN.test(url.pathname);
}

function isCacheable(response) {
  if (!response || !response.ok) return false;
  if (response.type !== "basic" && response.type !== "default") return false;
  const cacheControl = response.headers.get("Cache-Control") || "";
  if (/no-store|private/i.test(cacheControl)) return false;
  const vary = response.headers.get("Vary") || "";
  if (/cookie|authorization/i.test(vary)) return false;
  return true;
}

async function cacheResponse(request, response) {
  try {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response);
  } catch (error) {
    /* Quota or opaque-response failures must never break the request. */
  }
}

async function offlineResponse() {
  const precache = await caches.open(PRECACHE);
  const offline = await precache.match(OFFLINE_URL);
  if (offline) return offline;
  return new Response(OFFLINE_FALLBACK_HTML, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" }
  });
}

async function networkWithOfflineFallback(request) {
  try {
    return await fetch(request);
  } catch (error) {
    return offlineResponse();
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (isCacheable(response)) {
      await cacheResponse(request, response.clone());
    }
    return response;
  } catch (error) {
    const cache = await caches.open(STATIC_CACHE);
    const cached = await cache.match(request);
    return cached || Response.error();
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);

  const network = fetch(request)
    .then((response) => {
      if (isCacheable(response)) {
        return cacheResponse(request, response.clone()).then(() => response);
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    // Refresh in the background, but never block the page on the network.
    network.catch(() => undefined);
    return cached;
  }

  const response = await network;
  return response || Response.error();
}

self.addEventListener("fetch", (event) => {
  const request = event.request;

  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    // Authenticated HTML is deliberately network-only. On failure the user gets
    // a branded offline notice, never a cached dashboard or stale balances.
    event.respondWith(networkWithOfflineFallback(request));
    return;
  }

  if (request.headers.has("range")) return;

  // Only public static assets may be cached. Private APIs, PDF exports,
  // profile media and authentication routes remain network-only.
  if (!isStaticAsset(url)) return;

  if (isCodeAsset(url)) {
    event.respondWith(networkFirst(request));
    return;
  }

  event.respondWith(staleWhileRevalidate(request));
});
