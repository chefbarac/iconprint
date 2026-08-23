const PREFIX = "iconprint-cache";
const CACHE_NAME = PREFIX + "-v2.0.24";
const PRECACHE_URLS = ["./"];

// Local print/scan agent — never intercept or cache these.
function isAgentRequest(request) {
    try {
        const url = new URL(request.url);
        // Direct agent host
        if (
            (url.hostname === "localhost" || url.hostname === "127.0.0.1") &&
            (url.port === "5001" || url.port === "")
        ) {
            // Port 5001 is the agent; empty port only if path looks like agent API on same host
            if (url.port === "5001") return true;
        }
        if (url.port === "5001") return true;

        // Agent API paths (same-origin proxy or agent served under these routes)
        const path = url.pathname || "";
        if (
            path === "/health" ||
            path.startsWith("/health?") ||
            path === "/devices" ||
            path.startsWith("/devices?") ||
            path === "/scan" ||
            path.startsWith("/scan?") ||
            path === "/print" ||
            path.startsWith("/print/") ||
            path.startsWith("/api/")
        ) {
            return true;
        }
    } catch (e) {
        /* ignore */
    }
    return false;
}

self.addEventListener("install", (event) => {
    self.skipWaiting();
    if (PRECACHE_URLS.length) {
        event.waitUntil(
            caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
        );
    }
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((keys) =>
                Promise.all(
                    keys
                        .filter((key) => key.startsWith(PREFIX) && key !== CACHE_NAME)
                        .map((key) => caches.delete(key))
                )
            )
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const { request } = event;
    if (request.method !== "GET") return;

    // Agent / local backend: do not call respondWith — browser goes straight to network.
    // Never cache these responses.
    if (isAgentRequest(request)) return;

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
