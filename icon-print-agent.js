// ============================================================================
// icon-print-agent.js
//
// Shared browser-side helpers for talking to the local Icon Print Agent
// (scan.py). Every app (index.html and everything under moreapps/) loads this
// as a plain, non-module <script> BEFORE its own inline <script>, so the
// declarations below become part of the shared global scope: apps keep
// calling getAgentHost(), agentFetch(), promptAgentHost(), pollPrintJob(),
// and the LOCAL_AGENT variable, exactly as before — nothing in the apps'
// per-app code needs an "IconPrintAgent." prefix.
//
// This file intentionally does NOT wrap itself in an IIFE: an IIFE would
// hide these from the apps that depend on them.
//
// Load order matters. In each HTML file:
//   index.html        <script src="js/icon-print-agent.js"></script>
//   moreapps/*.html    <script src="../js/icon-print-agent.js"></script>
// placed immediately before the app's own <script> block.
// ============================================================================

// ---------- Agent host (configurable) ----------

var AGENT_HOST_KEY = "iconprint_agent_host";
var DEFAULT_AGENT_HOST = "http://localhost:5001";

function getAgentHost() {
    try {
        var v = (localStorage.getItem(AGENT_HOST_KEY) || "").trim();
        if (v) return v.replace(/\/+$/, "");
    } catch (e) { }
    return DEFAULT_AGENT_HOST;
}

function setAgentHost(url) {
    try {
        var cleaned = (url || "").trim().replace(/\/+$/, "");
        if (cleaned) localStorage.setItem(AGENT_HOST_KEY, cleaned);
        else localStorage.removeItem(AGENT_HOST_KEY);
    } catch (e) { }
}

var LOCAL_AGENT = getAgentHost();

// ---------- Agent token (optional) ----------
//
// Matches ICON_AGENT_TOKEN on the agent side (scan.py). Empty by default, so
// behaviour is unchanged unless the operator sets one via the agent-host
// prompt (click the agent badge in any app).

var AGENT_TOKEN_KEY = "iconprint_agent_token";

function getAgentToken() {
    try {
        return (localStorage.getItem(AGENT_TOKEN_KEY) || "").trim();
    } catch (e) { }
    return "";
}

function setAgentToken(token) {
    try {
        var cleaned = (token || "").trim();
        if (cleaned) localStorage.setItem(AGENT_TOKEN_KEY, cleaned);
        else localStorage.removeItem(AGENT_TOKEN_KEY);
    } catch (e) { }
}

// Every agent call goes through here so the token (when set) is always
// attached. With no token configured this is a plain fetch(LOCAL_AGENT + path).
function agentFetch(path, options) {
    options = options || {};
    var token = getAgentToken();
    if (token) {
        var headers = {};
        if (options.headers) {
            for (var k in options.headers) headers[k] = options.headers[k];
        }
        headers["X-Agent-Token"] = token;
        options = Object.assign({}, options, { headers: headers });
    }
    return fetch(LOCAL_AGENT + path, options);
}

// ---------- Host/token configuration prompt (click the agent badge) ----------
//
// Each app's badge-click handler calls this unqualified promptAgentHost().
// The typeof-guards below reference app-specific globals (agentConnectionEnabled,
// startAgentPolling, checkAgentHealth, agentOnline) that live in the app's own
// inline script — they're only referenced here, not required, so this works
// across apps whose badge/health-check wiring differs slightly.
function promptAgentHost() {
    var current = getAgentHost();
    var next = window.prompt(
        "Print agent URL\n\nUse this PC: http://localhost:5001\nOther PC on LAN: http://<agent-ip>:5001",
        current
    );
    if (next === null) return;
    setAgentHost(next || DEFAULT_AGENT_HOST);

    var currentToken = getAgentToken();
    var nextToken = window.prompt(
        "Agent token (leave blank if the agent doesn't require one)",
        currentToken
    );
    if (nextToken !== null) setAgentToken(nextToken);

    LOCAL_AGENT = getAgentHost();

    if (typeof agentConnectionEnabled !== "undefined" && agentConnectionEnabled && typeof startAgentPolling === "function") {
        startAgentPolling();
    } else if (typeof checkAgentHealth === "function") {
        checkAgentHealth();
    }

    var badgeText = document.getElementById("agentBadgeText");
    if (badgeText && typeof agentOnline !== "undefined" && !agentOnline) {
        badgeText.title = "Agent host: " + LOCAL_AGENT + " (click to change)";
    }
}

// ---------- Print job polling ----------
//
// Polls GET /print/jobs/<id> until the job completes, fails, or times out.
// statusEl (optional) is updated directly — it's expected to carry the same
// "print-status" / "print-status ok" / "print-status error" classes every
// app already uses, so no app-specific CSS or markup changes are needed.
//
// Resolves with the final job object on "completed".
// Rejects with an Error on "failed" or on timeout; the job (when available)
// is attached as err.job so callers can read err.job.warnings, etc.
//
// opts:
//   maxTries      - default 120
//   firstDelayMs  - delay before the first poll (default 600)
//   intervalMs    - delay between subsequent polls (default 1000)
function pollPrintJob(jobId, statusEl, opts) {
    opts = opts || {};
    var maxTries = opts.maxTries || 120;
    var firstDelayMs = opts.firstDelayMs != null ? opts.firstDelayMs : 600;
    var intervalMs = opts.intervalMs != null ? opts.intervalMs : 1000;

    function setStatus(text, cls) {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.className = "print-status" + (cls ? " " + cls : "");
    }

    return new Promise(function (resolve, reject) {
        var tries = 0;

        function tick() {
            var delay = tries === 0 ? firstDelayMs : intervalMs;
            tries++;
            setTimeout(function () {
                agentFetch("/print/jobs/" + jobId, { cache: "no-store" })
                    .then(function (sr) {
                        if (!sr.ok) throw new Error("Status check failed (" + sr.status + ")");
                        return sr.json();
                    })
                    .then(function (job) {
                        var status = job.status || "unknown";
                        setStatus(
                            status.charAt(0).toUpperCase() + status.slice(1) +
                            (job.warnings && job.warnings.length ? " — " + job.warnings[0] : ""),
                            status === "completed" ? "ok" : status === "failed" ? "error" : ""
                        );
                        if (status === "completed") {
                            resolve(job);
                            return;
                        }
                        if (status === "failed") {
                            var err = new Error(job.error || "Print job failed");
                            err.job = job;
                            reject(err);
                            return;
                        }
                        if (tries >= maxTries) {
                            reject(new Error("Print timed out waiting for job status"));
                            return;
                        }
                        tick();
                    })
                    .catch(function (err) {
                        // Transient status-check errors (network blip, brief 401 while a
                        // token is being (re)configured, etc.) are retried rather than
                        // failing the whole print — same behaviour as before this was
                        // shared, just centralized.
                        if (err && err.job) {
                            reject(err); // a genuine job failure — do not retry
                            return;
                        }
                        setStatus("Status check error: " + (err && err.message ? err.message : err), "error");
                        if (tries >= maxTries) {
                            reject(err instanceof Error ? err : new Error(String(err)));
                            return;
                        }
                        tick();
                    });
            }, delay);
        }

        tick();
    });
}