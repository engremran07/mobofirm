// Site search helpers shared by the header combobox: recent searches kept in this browser
// only (localStorage, never sent to the server, switchable off) and the "/" shortcut that
// focuses the header search field. See docs/04-backend/search.md.

const RECENT_KEY = "mf:recent-searches";
const OFF_KEY = "mf:recent-searches:off";
const MAX_RECENT = 6;
const MAX_LENGTH = 100;
const EDITABLE = "input, textarea, select, [contenteditable=''], [contenteditable='true']";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function recentSearchesEnabled() {
  const store = storage();
  return store !== null && store.getItem(OFF_KEY) !== "1";
}

export function setRecentSearchesEnabled(enabled) {
  const store = storage();
  if (store === null) return;
  if (enabled) {
    store.removeItem(OFF_KEY);
  } else {
    store.setItem(OFF_KEY, "1");
    store.removeItem(RECENT_KEY);
  }
}

export function readRecentSearches() {
  const store = storage();
  if (store === null || !recentSearchesEnabled()) return [];
  try {
    const parsed = JSON.parse(store.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item) => typeof item === "string" && item.trim() !== "").slice(0, MAX_RECENT)
      : [];
  } catch {
    return [];
  }
}

export function rememberSearch(query) {
  const store = storage();
  const text = String(query ?? "").trim().slice(0, MAX_LENGTH);
  if (store === null || !recentSearchesEnabled() || text.length < 2) return;
  const others = readRecentSearches().filter((item) => item.toLowerCase() !== text.toLowerCase());
  store.setItem(RECENT_KEY, JSON.stringify([text, ...others].slice(0, MAX_RECENT)));
}

export function clearRecentSearches() {
  const store = storage();
  if (store !== null) store.removeItem(RECENT_KEY);
}

function globalSearchInput() {
  const inputs = document.querySelectorAll('[data-global-search] input[role="combobox"]');
  return Array.from(inputs).find((input) => input.offsetParent !== null) ?? null;
}

function syncRecentToggles() {
  document.querySelectorAll("[data-recent-toggle]").forEach((toggle) => {
    const enabled = recentSearchesEnabled();
    toggle.textContent = enabled ? "Turn off recent searches" : "Turn on recent searches";
    toggle.setAttribute("data-recent-toggle", enabled ? "off" : "on");
    toggle.hidden = false;
  });
}

// "/" (or Ctrl/Cmd+K) focuses the header search unless the visitor is typing somewhere.
export function initializeSearchShortcuts() {
  syncRecentToggles();
  document.addEventListener("htmx:afterSwap", syncRecentToggles);
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey) return;
    const slash = event.key === "/" && !event.ctrlKey && !event.metaKey;
    const palette = event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey);
    if (!slash && !palette) return;
    const target = event.target;
    if (slash && target instanceof Element && target.closest(EDITABLE)) return;
    const input = globalSearchInput();
    if (input === null) return;
    event.preventDefault();
    input.focus();
    input.select();
  });
  document.addEventListener("click", (event) => {
    const toggle = event.target instanceof Element ? event.target.closest("[data-recent-toggle]") : null;
    if (toggle === null) return;
    const enable = toggle.getAttribute("data-recent-toggle") === "on";
    setRecentSearchesEnabled(enable);
    syncRecentToggles();
  });
}
