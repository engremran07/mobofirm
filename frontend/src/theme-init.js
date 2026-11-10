// Classic (non-module) script loaded synchronously in <head> so the resolved theme
// is applied before first paint. Storage access is guarded: blocked storage must
// never stop the rest of the frontend from running.
(function () {
  var root = document.documentElement;
  var stored = null;
  var contrast = null;
  try {
    stored = window.localStorage.getItem("mobofirm-theme");
    contrast = window.localStorage.getItem("mobofirm-contrast");
  } catch (error) {
    stored = null;
    contrast = null;
  }
  function matches(query) {
    return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  }
  var explicit = stored === "dark" || stored === "light" ? stored : null;
  var theme = explicit || (matches("(prefers-color-scheme: dark)") ? "dark" : "light");
  root.setAttribute("data-bs-theme", theme);
  root.setAttribute("data-theme-mode", explicit || "auto");
  if (contrast === "high" || (contrast !== "normal" && matches("(prefers-contrast: more)"))) {
    root.setAttribute("data-contrast", "high");
  }
  root.classList.add("js");
})();
