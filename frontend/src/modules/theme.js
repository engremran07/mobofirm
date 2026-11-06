import { renderIcons } from "./icons.js";

const STORAGE_KEY = "mobofirm-theme";

function readStoredTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch (error) {
    return null;
  }
}

function writeStoredTheme(theme) {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch (error) {
    // Storage can be blocked; the theme still applies for this page view.
  }
}

function systemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.bsTheme = theme;
  const target = theme === "dark" ? "light" : "dark";
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.setAttribute("aria-label", `Switch to ${target} theme`);
  });
  renderIcons();
}

export function initializeTheme() {
  applyTheme(readStoredTheme() ?? systemTheme());

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-theme-toggle]");
    if (!toggle) return;
    const next = document.documentElement.dataset.bsTheme === "dark" ? "light" : "dark";
    writeStoredTheme(next);
    applyTheme(next);
  });

  // Follow the operating-system theme until the visitor makes an explicit choice.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (readStoredTheme() === null) applyTheme(systemTheme());
  });
}
