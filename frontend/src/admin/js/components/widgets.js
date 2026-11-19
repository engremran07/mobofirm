// Dashboard widgets: lazy-loaded, self-refreshing panels with loading / error / retry states.
//
// Markup contract (templates/console/dashboard/*, admin/index.html):
//   [data-console-widget="slug"]            the placeholder (hx-trigger="load") and the loaded fragment
//   [data-console-refresh="seconds"]        loaded fragment only: refresh interval, paused while the tab is hidden
//   [data-console-widget-error]             hidden error block with a Retry button (its own hx-get); shown on failure
// State is published as data-state="loading|ready|error" (+ aria-busy), so CSS owns the presentation.
// A refresh is requested with the element-local `console-widget-refresh` event the fragment listens for in its
// hx-trigger; `hx-sync="this:replace"` on the fragment cancels a stale request. A failed request never leaves a
// skeleton behind: the error block replaces it, and automatic refresh stays off until the user presses Retry.
import { htmx } from "../htmx-setup.js";
import { EVENTS } from "../lib/util.js";

const REGION = "[data-console-widget]";
const REFRESH_EVENT = "console-widget-refresh";
const TICK_MS = 10000;
const MIN_SECONDS = 15;
const loadedAt = new Map();

const regionOf = (element) => (element instanceof Element ? element.closest(REGION) : null);

function setState(region, state) {
  region.dataset.state = state;
  region.setAttribute("aria-busy", String(state === "loading"));
  const error = region.querySelector(":scope [data-console-widget-error]");
  if (error) error.hidden = state !== "error";
}

function widgetFor(ctx) {
  return regionOf(ctx?.sourceElement) ?? regionOf(ctx?.target);
}

function onBeforeRequest(event) {
  const region = widgetFor(event.detail?.ctx);
  if (region) setState(region, "loading");
}

function onFailure(event) {
  const region = widgetFor(event.detail?.ctx);
  if (region) setState(region, "error");
}

function onAfterSwap(event) {
  const ctx = event.detail?.ctx;
  const region = regionOf(ctx?.target);
  const slug = region?.dataset.consoleWidget;
  if (slug) loadedAt.set(slug, Date.now());
}

function tick() {
  if (document.hidden) return;
  const now = Date.now();
  for (const region of document.querySelectorAll(`${REGION}[data-console-refresh]`)) {
    if (region.dataset.state === "loading" || region.dataset.state === "error") continue;
    const seconds = Math.max(MIN_SECONDS, Number(region.dataset.consoleRefresh) || 0);
    const slug = region.dataset.consoleWidget;
    if (!loadedAt.has(slug)) loadedAt.set(slug, now);
    if (now - loadedAt.get(slug) >= seconds * 1000) {
      loadedAt.set(slug, now);
      htmx.trigger(region, REFRESH_EVENT);
    }
  }
}

export function initWidgets() {
  document.addEventListener("htmx:before:request", onBeforeRequest);
  document.addEventListener("htmx:response:error", onFailure);
  document.addEventListener("htmx:error", onFailure);
  document.addEventListener("htmx:after:swap", onAfterSwap);
  document.addEventListener("visibilitychange", tick);
  document.addEventListener(EVENTS.refresh, () => loadedAt.clear());
  window.setInterval(tick, TICK_MS);
}
