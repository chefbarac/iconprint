const PREFIX = 'iconprint-cache';
const CACHE_NAME = PREFIX + '-v2.0.4';
const PRECACHE_URLS = [
    './',
];

self.addEventListener('install', (event) => {
    self.skipWaiting();
    if (PRECACHE_URLS.length) {
        event.waitUntil(
            caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
        );
    }
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((key) => key.startsWith(PREFIX) && key !== CACHE_NAME)
                    .map((key) => caches.delete(key))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;
    if (request.method !== 'GET') return;
    event.respondWith(cacheFirst(request));
});

async function cacheFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    const cachedResponse = await cache.match(request);

    // Have it cached — serve it, no network call at all.
    if (cachedResponse) return cachedResponse;

    // Not cached yet — fetch from network and cache for next time.
    const networkResponse = await fetch(request);
    if (networkResponse && networkResponse.status === 200) {
        cache.put(request, networkResponse.clone());
    }
    return networkResponse;
}