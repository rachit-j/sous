// Kitchen view (docs/v3.md F1): large camera and conversation, distance-readable overlay,
// screen wake lock and optional full screen. Reads state the other scripts already render;
// it never calls the API or changes cooking state.
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const STORAGE_KEY = "sous.kitchenView"; // UI layout preference only; never secrets
  const CAPTION_MS = 9000;
  let wakeLock = null;
  let wakeLockPending = false;
  let captionTimer = null;

  function preferred() {
    try { return localStorage.getItem(STORAGE_KEY) !== "0"; } catch { return true; }
  }

  function applyMode(on) {
    $("#session-panel").classList.toggle("kitchen-mode", on);
    const toggle = $("#kitchen-view-toggle");
    toggle.setAttribute("aria-pressed", String(on));
    toggle.textContent = on ? "Standard view" : "Kitchen view";
  }

  function cooking() {
    const panel = $("#session-panel");
    return Boolean(panel && !panel.hidden && document.visibilityState === "visible");
  }

  async function syncWakeLock() {
    if (!("wakeLock" in navigator)) return;
    if (cooking() && !wakeLock && !wakeLockPending) {
      wakeLockPending = true;
      try {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } catch { /* denied or unsupported here; the screen may still sleep */ }
      finally { wakeLockPending = false; }
      if (!cooking()) syncWakeLock();
    } else if (!cooking() && wakeLock) {
      const lock = wakeLock;
      wakeLock = null;
      try { await lock.release(); } catch { /* already released */ }
    }
  }

  function syncFullscreen() {
    const button = $("#fullscreen-toggle");
    button.hidden = !document.fullscreenEnabled;
    const active = document.fullscreenElement === $("#session-panel");
    button.textContent = active ? "Exit full screen" : "Full screen";
    if (document.fullscreenElement && !cooking()) document.exitFullscreen().catch(() => {});
  }

  function syncOverlay() {
    if (!cooking()) return;
    const count = $("#step-count")?.textContent || "";
    const title = $("#step-title")?.textContent || "";
    $("#overlay-step").textContent = title ? `${count} · ${title}` : count;
    const readout = $("#timer-readout");
    const timerState = readout?.dataset.state || "idle";
    const timer = $("#overlay-timer");
    timer.hidden = timerState === "idle";
    timer.textContent = readout?.textContent || "";
    timer.dataset.state = timerState;
    const health = $("#monitoring-health");
    const status = $("#overlay-status");
    status.textContent = health?.textContent || "";
    status.dataset.state = health?.dataset.state || "unknown";
    status.hidden = !status.textContent;
  }

  function showCaption(text) {
    const caption = $("#overlay-caption");
    if (!caption || !text) return;
    caption.textContent = text;
    caption.hidden = false;
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => { caption.hidden = true; caption.textContent = ""; }, CAPTION_MS);
  }

  function tick() {
    syncOverlay();
    syncWakeLock();
    syncFullscreen();
  }

  function bind() {
    applyMode(preferred());
    $("#kitchen-view-toggle").addEventListener("click", () => {
      const on = !$("#session-panel").classList.contains("kitchen-mode");
      applyMode(on);
      try { localStorage.setItem(STORAGE_KEY, on ? "1" : "0"); } catch { /* storage unavailable */ }
    });
    $("#fullscreen-toggle").addEventListener("click", () => {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else $("#session-panel").requestFullscreen?.().catch(() => {});
    });
    // Captions for what Sous says: browser speech (app.js) and the voice companion's replies.
    document.addEventListener("sous:spoken", (event) => showCaption(event.detail?.text));
    document.addEventListener("visibilitychange", tick);
    document.addEventListener("fullscreenchange", syncFullscreen);
    document.addEventListener("sous:unauthorized", () => { $("#overlay-caption").hidden = true; syncWakeLock(); });
    setInterval(tick, 500);
    tick();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
