`

<!-- Google tag (gtag.js) -->

        <script async src="https://www.googletagmanager.com/gtag/js?id=G-SWGDSZH41W"></script>
        <script>
            window.dataLayer = window.dataLayer || [];
            function gtag() {
                dataLayer.push(arguments);
            }
            gtag("js", new Date());

            gtag("config", "G-SWGDSZH41W");
        </script>

`

`

<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🖨️</text></svg>' />
`

`

<link
            rel="icon"
            href='data:image/svg+xml,<svg  xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">
                            <rect x="2" y="2" width="12" height="12" rx="1" />
                            <path d="M2 11l3-3 2 2 4-5 3 4" />
                        </svg>'
        />
`

`

<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🧩</text></svg>' />
`

`

<script src="sw-register.js"></script>

`

`

<script>
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
        </script>

`
