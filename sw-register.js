if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("sw.js").catch((err) => {
            console.error("SW registration failed:", err);
        });
    });

    // Auto-refresh once when a new SW version takes control.
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (refreshing) return;
        refreshing = true;
        window.location.reload();
    });
}