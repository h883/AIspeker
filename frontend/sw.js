/* Service Worker（仕様書 26章）。
   画面の骨組みだけをキャッシュし、API 応答はキャッシュしない。
   古い天気や予定を表示してしまわないようにするため（仕様書 22章）。 */

const CACHE = "student-ai-v2";
const SHELL = [
  "/",
  "/index.html",
  "/css/style.css",
  "/js/api.js",
  "/js/speech.js",
  "/js/wakeword.js",
  "/js/app.js",
  "/assets/icon.svg",
  "/assets/icon-192.png",
  "/manifest.webmanifest"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;  // 実データは必ずネットワークから

  event.respondWith(
    caches.match(event.request).then(cached => {
      const network = fetch(event.request)
        .then(response => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then(cache => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
