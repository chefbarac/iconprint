`<!-- Google tag (gtag.js) -->

        <script async src="https://www.googletagmanager.com/gtag/js?id=G-SWGDSZH41W"></script>
        <script>
            window.dataLayer = window.dataLayer || [];
            function gtag() {
                dataLayer.push(arguments);
            }
            gtag("js", new Date());

            gtag("config", "G-SWGDSZH41W");
        </script>`

`<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🖨️</text></svg>' />`

`<link
            rel="icon"
            href='data:image/svg+xml,<svg  xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">
                            <rect x="2" y="2" width="12" height="12" rx="1" />
                            <path d="M2 11l3-3 2 2 4-5 3 4" />
                        </svg>'
        />`

`<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🧩</text></svg>' />`

`<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🎞️</text></svg>' />`

`<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🪪</text></svg>' />`

`<script>
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
        </script>`

`source .venv/scripts/activate`

`pip install flask flask-cors pywin32 pillow`

`let audioCtx;

            async function playPing() {
                try {
                    if (!audioCtx) {
                        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    }

                    if (audioCtx.state === "suspended") {
                        await audioCtx.resume();
                    }

                    const notes = [
                        { freq: 880, duration: 0.18 },
                        { freq: 1175, duration: 0.18 },
                        { freq: 1568, duration: 0.35 },
                    ];

                    let start = audioCtx.currentTime;

                    notes.forEach(({ freq, duration }) => {
                        const osc = audioCtx.createOscillator();
                        const gain = audioCtx.createGain();

                        osc.type = "triangle";
                        osc.frequency.value = freq;

                        gain.gain.setValueAtTime(0.0001, start);
                        gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
                        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

                        osc.connect(gain);
                        gain.connect(audioCtx.destination);

                        osc.start(start);
                        osc.stop(start + duration);

                        start += duration * 0.85;
                    });
                } catch (err) {
                    console.error(err);
                }
            }`

`pm2 start scan.py --interpreter C:\Users\chefr\iconprint\.venv\Scripts\python.exe --name scanner-backend`
