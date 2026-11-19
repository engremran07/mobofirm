// Colour mode: light | dark | system, persisted and shared with the public site.
//
// Storage keys match the public theme runtime (modules/theme.js) so one choice follows the
// person across the site and console: `mobofirm-theme` = "light" | "dark" (absent = system),
// `mobofirm-contrast` = "high" | "normal". Attributes on <html>:
//   data-bs-theme      resolved theme, "light" | "dark"   (Bootstrap + tokens key off this)
//   data-theme-mode    the choice, "light" | "dark" | "auto"
//   data-contrast      "high" when high contrast is on
// No flash: the existing classic script `build/theme-init.js` (<1 KB, synchronous in <head>)
// applies the stored choice before first paint; this module owns the controls afterwards and
// resolves identically, so it is safe if that script is absent (CSS should then default to
// prefers-color-scheme).
import { EVENTS, emit, storage } from "../lib/util.js";

const THEME_KEY = "mobofirm-theme";
const CONTRAST_KEY = "mobofirm-contrast";
const MODES = new Set(["light", "dark", "auto"]);
const media = (query) => window.matchMedia(query);

const storedMode = () => {
  const value = storage.get(THEME_KEY);
  return value === "light" || value === "dark" ? value : "auto";
};
const resolve = (mode) => (mode === "auto" ? (media("(prefers-color-scheme: dark)").matches ? "dark" : "light") : mode);
const highContrast = () => {
  const value = storage.get(CONTRAST_KEY);
  return value === "high" || (value !== "normal" && media("(prefers-contrast: more)").matches);
};

export const normalizeMode = (value) => {
  const mode = value === "system" ? "auto" : value;
  return MODES.has(mode) ? mode : "auto";
};

export function themeState() {
  const root = document.documentElement;
  const mode = normalizeMode(root.dataset.themeMode ?? storedMode());
  return { mode, resolved: root.dataset.bsTheme ?? resolve(mode), contrast: root.dataset.contrast === "high" };
}

function syncControls() {
  const { mode, resolved, contrast } = themeState();
  for (const button of document.querySelectorAll("[data-console-theme-set]")) {
    button.setAttribute("aria-pressed", String(normalizeMode(button.dataset.consoleThemeSet) === mode));
  }
  for (const button of document.querySelectorAll("[data-console-theme-cycle]")) {
    const next = resolved === "dark" ? "light" : "dark";
    if (!button.textContent.trim()) button.setAttribute("aria-label", `Switch to ${next} theme`);
  }
  for (const button of document.querySelectorAll("[data-console-contrast-toggle]")) {
    button.setAttribute("aria-pressed", String(contrast));
  }
}

export function applyTheme(value) {
  const mode = normalizeMode(value);
  const root = document.documentElement;
  root.dataset.themeMode = mode;
  root.dataset.bsTheme = resolve(mode);
  syncControls();
  emit(EVENTS.themeChange, themeState());
}

export function setTheme(value) {
  const mode = normalizeMode(value);
  storage.set(THEME_KEY, mode === "auto" ? null : mode);
  applyTheme(mode);
}

export function setContrast(high) {
  storage.set(CONTRAST_KEY, high ? "high" : "normal");
  const root = document.documentElement;
  if (high) root.dataset.contrast = "high";
  else delete root.dataset.contrast;
  syncControls();
  emit(EVENTS.themeChange, themeState());
}

export function initTheme() {
  applyTheme(storedMode());
  if (highContrast()) document.documentElement.dataset.contrast = "high";
  syncControls();

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const setter = event.target.closest("[data-console-theme-set]");
    if (setter instanceof HTMLElement) {
      setTheme(setter.dataset.consoleThemeSet);
      return;
    }
    if (event.target.closest("[data-console-theme-cycle]")) {
      setTheme(themeState().resolved === "dark" ? "light" : "dark");
      return;
    }
    if (event.target.closest("[data-console-contrast-toggle]")) setContrast(!themeState().contrast);
  });
  media("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (storedMode() === "auto") applyTheme("auto");
  });
  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) applyTheme(storedMode());
    if (event.key === CONTRAST_KEY) setContrast(highContrast());
  });
  // Controls swapped in later (menus, dialogs) pick up their pressed state.
  document.addEventListener("htmx:after:swap", syncControls);
}

// x-data="consoleTheme": reactive mirror for markup that wants `mode` / `resolved` / `is('dark')`.
export function themeComponent() {
  return {
    mode: "auto",
    resolved: "light",
    contrast: false,
    init() {
      this.sync = () => Object.assign(this, themeState());
      this.sync();
      document.addEventListener(EVENTS.themeChange, this.sync);
    },
    destroy() {
      document.removeEventListener(EVENTS.themeChange, this.sync);
    },
    is(name) {
      return this.mode === normalizeMode(name);
    },
    get label() {
      return this.mode === "auto" ? "System theme" : `${this.mode === "dark" ? "Dark" : "Light"} theme`;
    },
    choose(event) {
      setTheme(event.currentTarget?.dataset?.consoleThemeSet);
    },
  };
}
