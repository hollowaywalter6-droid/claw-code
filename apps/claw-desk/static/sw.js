/* Cache only public interface assets; never cache any /api response or message. */
const CACHE = "claw-desk-static-v3";
const ASSETS = ["/", "/app.css", "/app.js", "/manifest.webmanifest", "/favicon.svg", "/icon-180.png"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const u = new URL(event.request.url);
  if (event.request.method !== "GET" || u.origin !== self.location.origin || !ASSETS.includes(u.pathname)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); }
    return response;
  }).catch(() => caches.match(event.request)));
});
