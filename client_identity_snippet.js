/**
 * client-identity.js
 * -------------------
 * Drop this into the frontend that calls the scan.py backend (the page
 * running on each PC / laptop). It lets each machine "introduce itself"
 * once, then automatically tags every /scan and /print request with an
 * X-Client-Name header so the backend's client_name / client_ip metrics
 * are populated correctly.
 *
 * The backend already records client_ip automatically (no code needed for
 * that part) — this only adds the friendly name on top, e.g. "Laptop"
 * instead of just "192.168.1.23".
 */

const CLIENT_NAME_KEY = "icon_agent_client_name";

/** Get the stored name for this PC's browser, or null if never set. */
function getClientName() {
  return localStorage.getItem(CLIENT_NAME_KEY);
}

/** Save a friendly name for this PC's browser (persists across reloads). */
function setClientName(name) {
  const clean = (name || "").trim().slice(0, 100);
  if (clean) {
    localStorage.setItem(CLIENT_NAME_KEY, clean);
  }
  return clean;
}

/**
 * Ask the user to name this PC the first time the page loads on it.
 * Call this once on app startup. Safe to call every time — it's a no-op
 * once a name is already stored.
 */
function ensureClientNamePrompted() {
  if (!getClientName()) {
    const suggested =
      window.matchMedia && window.matchMedia("(pointer: coarse)").matches
        ? "Tablet"
        : "PC";
    const name = window.prompt(
      "Name this computer (e.g. 'Laptop', 'Front Desk PC') so scan/print " +
        "activity can be tracked per machine:",
      suggested
    );
    if (name) setClientName(name);
  }
}

/**
 * Wrapper around fetch() that automatically attaches the X-Client-Name
 * header. Use this instead of raw fetch() for calls to the agent
 * (http://localhost:5001/scan, /print, etc.).
 */
async function agentFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const name = getClientName();
  if (name) headers.set("X-Client-Name", name);
  return fetch(url, { ...options, headers });
}

// ---------------------------------------------------------------------
// Example usage
// ---------------------------------------------------------------------

// On page load:
// ensureClientNamePrompted();

// Scanning:
// const res = await agentFetch("http://localhost:5001/scan", {
//   method: "POST",
//   headers: { "Content-Type": "application/json" },
//   body: JSON.stringify({ size: "letter", image_type: "color" }),
// });

// Printing (JSON body):
// const res = await agentFetch("http://localhost:5001/print", {
//   method: "POST",
//   headers: { "Content-Type": "application/json" },
//   body: JSON.stringify({ printer_id: "MyPrinter", image_base64: "..." }),
// });

// Printing (multipart form) — the header still works the same way,
// just don't set Content-Type yourself (let the browser set the
// multipart boundary):
// const form = new FormData();
// form.append("printer_id", "MyPrinter");
// form.append("image", fileBlob);
// const res = await agentFetch("http://localhost:5001/print", {
//   method: "POST",
//   body: form,
// });

// Optional: let the user rename this PC later, e.g. from a settings menu:
// setClientName("Reception Laptop");