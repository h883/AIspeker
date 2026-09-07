/* Service Worker（仕様書 26章）。
   ホーム画面に追加してアプリのように使えるようにするためのもの。

   方針:
   - 常にネットワーク優先。Raspberry Pi に届くときは必ず最新の画面を出す。
   - インストール時に先読みキャッシュはしない。
     先読みすると、アプリを更新しても古い画面を掴み続ける事故が起きるため。
   - API 応答はキャッシュしない（古い天気や予定を表示しないため。仕様書 22章）。
   - 通信できないときだけ、以前に開いた画面をキャッシュから出す。 */

const CACHE = "student-ai-v4";

self.addEventListener("install", () => self.skipWaiting());

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
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => cached || Response.error()))
  );
});
