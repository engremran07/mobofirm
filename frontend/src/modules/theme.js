// Theme runtime: colour mode (auto | light | dark) and the high-contrast option.
// theme-init.js applies the stored choice before first paint; this module owns the
// controls afterwards. Attributes on <html>:
//   data-bs-theme       resolved theme, "light" or "dark" (drives tokens + Bootstrap)
//   data-theme-mode     the visitor's choice: "auto", "light" or "dark"
//   data-contrast       "high" when the high-contrast colour tokens are active
// Storage can be blocked; every access is guarded and the choice still applies for the page view.
import { renderIcons } from "./icons.js";

const STORAGE_KEY = "mobofirm-theme";
const CONTRAST_KEY = "mobofirm-contrast";

function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function write(key, value) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch (error) {
    // The choice still applies for this page view.
  }
}

const query = (text) => window.matchMedia(text).matches;
const storedMode = () => {
  const value = read(STORAGE_KEY);
  return value === "dark" || value === "light" ? value : "auto";
};
const systemTheme = () => (query("(prefers-color-scheme: dark)") ? "dark" : "light");

function isHighContrast() {
  const value = read(CONTRAST_KEY);
  return value === "high" || (value !== "normal" && query("(prefers-contrast: more)"));
}

function syncControls() {
  const root = document.documentElement;
  const dark = root.dataset.bsTheme === "dark";
  const mode = root.dataset.themeMode ?? "auto";
  const contrast = root.dataset.contrast === "high";
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    // Icon-only toggles need a label; buttons with visible text keep their own name.
    if (!button.textContent.trim()) {
      button.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} theme`);
    }
  });
  document.querySelectorAll("[data-theme-set]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeSet === mode));
  });
  document.querySelectorAll("[data-contrast-toggle]").forEach((button) => {
    button.setAttribute("aria-pressed", String(contrast));
  });
}

export function applyTheme(mode) {
  const root = document.documentElement;
  root.dataset.themeMode = mode;
  root.dataset.bsTheme = mode === "auto" ? systemTheme() : mode;
  syncControls();
  renderIcons();
}

export function applyContrast(high) {
  const root = document.documentElement;
  if (high) root.dataset.contrast = "high";
  else delete root.dataset.contrast;
  syncControls();
}

export function setThemeMode(mode) {
  write(STORAGE_KEY, mode === "auto" ? null : mode);
  applyTheme(mode);
}

export function setContrast(high) {
  write(CONTRAST_KEY, high ? "high" : "normal");
  applyContrast(high);
}

export function initializeTheme() {
  applyTheme(storedMode());
  applyContrast(isHighContrast());

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const toggle = event.target.closest("[data-theme-toggle]");
    if (toggle) {
      setThemeMode(document.documentElement.dataset.bsTheme === "dark" ? "light" : "dark");
      return;
    }
    const setter = event.target.closest("[data-theme-set]");
    if (setter) {
      setThemeMode(setter.dataset.themeSet);
      return;
    }
    if (event.target.closest("[data-contrast-toggle]")) {
      setContrast(document.documentElement.dataset.contrast !== "high");
    }
  });

  // "Auto" follows the operating system live; explicit choices never change.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (storedMode() === "auto") applyTheme("auto");
  });
  window.matchMedia("(prefers-contrast: more)").addEventListener("change", () => {
    if (read(CONTRAST_KEY) === null) applyContrast(isHighContrast());
  });
  // Keep other open tabs in step.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY) applyTheme(storedMode());
    if (event.key === CONTRAST_KEY) applyContrast(isHighContrast());
  });
}
