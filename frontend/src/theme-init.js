// Classic (non-module) script loaded synchronously in <head> so the resolved theme
// is applied before first paint. Storage access is guarded: blocked storage must
// never stop the rest of the frontend from running.
(function () {
  var root = document.documentElement;
  var stored = null;
  try {
    stored = window.localStorage.getItem("mobofirm-theme");
  } catch (error) {
    stored = null;
  }
  var prefersDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  var theme =
    stored === "dark" || stored === "light" ? stored : prefersDark ? "dark" : "light";
  root.setAttribute("data-bs-theme", theme);
  root.classList.add("js");
})();
