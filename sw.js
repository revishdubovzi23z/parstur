// 7.1 — версия кеша подставляется на сервере (см. main.py /sw.js).
// __SW_VERSION__ заменяется хешем mtime/size index.html, manifest.json,
// sw.js на каждый запрос; при изменении любого из них браузер видит новый
// файл сервис-воркера и переинициализирует кеш.
const CACHE_NAME = 'radar-__SW_VERSION__';

// Стратегия:
//   * navigation (HTML) — network-first, чтобы новый билд index.html сразу
//     попадал к пользователю; при оффлайне отдаём последний кеш.
//   * иконки/manifest — cache-first (быстро + работает оффлайн).
//   * всё остальное (CDN, API, постеры) — пропускаем мимо SW, чтобы не
//     ломать auth/streaming/CSP.
const CACHE_FIRST_PATHS = new Set(['/manifest.json', '/icon.png']);

self.addEventListener('install', (event) => {
  // Прогреваем кеш минимальным набором; навигационные ответы
  // подкладываются на лету уже в fetch-обработчике.
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(['/manifest.json', '/icon.png']))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Сносим все кеши прошлых версий, иначе старая страница могла бы
  // подняться при оффлайне после деплоя.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

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

  const isNavigation = req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html');

  if (isNavigation) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put('/', copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => caches.match('/').then((cached) => cached || Response.error()))
    );
    return;
  }

  if (CACHE_FIRST_PATHS.has(url.pathname)) {
    event.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return resp;
        })
      )
    );
  }
  // Всё остальное — пускаем напрямую, без кеширования.
});
