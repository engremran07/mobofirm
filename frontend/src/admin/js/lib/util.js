// Small DOM/storage helpers shared by every console module. No dependencies.

export const EVENTS = Object.freeze({
  toast: "console-toast",
  dialogOpen: "console-dialog-open",
  dialogClose: "console-dialog-close",
  dialogOpened: "console-dialog-opened",
  dialogClosed: "console-dialog-closed",
  refresh: "console-refresh",
  popupResponse: "console-popup-response",
  formsetAdded: "console-formset-added",
  formsetRemoved: "console-formset-removed",
  formsetMoved: "console-formset-moved",
  comboboxSync: "console-combobox-sync",
  reveal: "console-reveal",
  themeChange: "console-theme-change",
  sidebarChange: "console-sidebar-change",
  tabChange: "console-tab-change",
  selectionChange: "console-selection-change",
  densityChange: "console-density-change",
  paletteOpen: "console-palette-open",
  navigated: "console-navigated",
});

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export const storage = {
  get(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch {
      // Blocked storage: the choice still applies for this page view.
    }
  },
};

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function emit(name, detail = {}, target = document) {
  const event = new CustomEvent(name, { detail, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

export function isVisible(element) {
  return element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";
}

export function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(isVisible);
}

// True while the user is typing somewhere a single-key shortcut must not fire.
export function isTypingTarget(target) {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], .note-editor'));
}

export function debounce(fn, wait) {
  let timer = 0;
  const wrapped = (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
  wrapped.cancel = () => window.clearTimeout(timer);
  return wrapped;
}

let uid = 0;
export const nextId = (prefix) => `${prefix}-${++uid}`;

export function ensureId(element, prefix) {
  if (!element.id) element.id = nextId(prefix);
  return element.id;
}

// Polite live-region announcement for changes that have no visible text of their own.
export function announce(message) {
  let region = document.getElementById("console-announcer");
  if (!region) {
    region = document.createElement("div");
    region.id = "console-announcer";
    region.className = "visually-hidden";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "true");
    document.body.append(region);
  }
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = message;
  }, 30);
}

// Elements are bound at most once, whether Alpine (x-data) or the hook scanner gets there first.
const handles = new WeakMap();

export function bindOnce(element, attach) {
  if (handles.has(element)) return handles.get(element);
  const handle = attach(element) ?? {};
  handles.set(element, handle);
  return handle;
}

export function unbind(element) {
  handles.get(element)?.destroy?.();
  handles.delete(element);
}

// Alpine.data() factory whose only job is lifecycle: attach on init, clean up on destroy.
export function behaviour(attach) {
  return () => {
    let element = null;
    return {
      init() {
        element = this.$el;
        bindOnce(element, attach);
      },
      destroy() {
        if (element) unbind(element);
      },
    };
  };
}
