// 7.1 / ROADMAP 10.7z — the SW body is rendered by `main.py:/sw.js`,
// which substitutes `__SW_VERSION__` (sha1 of dist/index.html +
// sw.js + manifest.json) and `__SW_PRECACHE__` (JSON-encoded list of
// the SPA shell + Vite-emitted assets) at request time. Any change
// to those upstream files yields a different SW body, the browser
// reinstalls the worker, and the activate handler purges old caches.
const CACHE_NAME = 'radar-__SW_VERSION__';
const PRECACHE_URLS = __SW_PRECACHE__;

// Strategy:
//   * navigation (HTML) — network-first, so a fresh index.html reaches
//     the user immediately; falls back to the precached shell when
//     offline.
//   * /api/* GETs — network-first with cache fallback, so a flaky
//     network doesn't immediately blank the feed; non-GET requests
//     (mutations) always go straight to network — we never serve a
//     stale write from cache.
//   * icons/manifest/assets — cache-first (Vite content-hashes asset
//     filenames, so a stale cache entry can never be wrong).
//   * everything else (poster URLs from external hosts, etc.) is
//     passed through untouched.

self.addEventListener('install', (event) => {
  // ROADMAP 10.7z — precache the SPA shell + bundle on install so the
  // app is functional offline immediately after activation. The list
  // is rendered server-side and changes whenever the asset filenames
  // change, so old entries fall out naturally via the new CACHE_NAME.
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        // `addAll` is atomic — if any URL 404s the whole install fails
        // and the SW stays at the previous version. We deliberately
        // suppress that failure (catch) so a missing optional asset
        // doesn't brick the app; we still attempt to cache the rest.
        cache.addAll(PRECACHE_URLS).catch((err) => {
          console.warn('[sw] precache failed', err);
        }),
      )
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  // Drop every cache that isn't ours so the previous build's stale
  // bundle can't be served after a deploy.
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

const CACHE_FIRST_PATHS = new Set(['/manifest.json', '/favicon.png', '/icon-192.png', '/icon-512.png']);

function isAssetPath(pathname) {
  return pathname.startsWith('/assets/');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    return;
  }
  if (url.origin !== self.location.origin) return;

  const isNavigation =
    req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');

  if (isNavigation) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches
              .open(CACHE_NAME)
              .then((cache) => cache.put('/', copy))
              .catch(() => {});
          }
          return resp;
        })
        .catch(() =>
          caches.match('/').then((cached) => cached || Response.error()),
        ),
    );
    return;
  }

  if (url.pathname.startsWith('/api/')) {
    // ROADMAP 10.7z — network-first for API reads. If the network is
    // unavailable we fall back to whatever's in the cache, which is
    // usually nothing for /api/* (we don't actively cache responses),
    // so the typical failure case still surfaces as a real fetch
    // error to the SPA. The fallback is here for forward compat.
    event.respondWith(
      fetch(req).catch(() =>
        caches.match(req).then((cached) => cached || Response.error()),
      ),
    );
    return;
  }

  if (CACHE_FIRST_PATHS.has(url.pathname) || isAssetPath(url.pathname)) {
    event.respondWith(
      caches.match(req).then(
        (cached) =>
          cached ||
          fetch(req).then((resp) => {
            if (resp && resp.ok) {
              const copy = resp.clone();
              caches
                .open(CACHE_NAME)
                .then((cache) => cache.put(req, copy))
                .catch(() => {});
            }
            return resp;
          }),
      ),
    );
    return;
  }

  // Everything else (external CDNs, poster hosts) — pass-through, no
  // caching, no interception.
});
