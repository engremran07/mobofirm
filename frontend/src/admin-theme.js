// Classic (non-module) script loaded in the admin <head> before Alpine starts.
//
// Unfold keeps the admin colour theme ("light" | "dark" | "auto") in Alpine's
// $persist storage under `adminTheme` (JSON encoded). This script (1) applies the
// stored choice before first paint, (2) adopts the public site's explicit
// `mobofirm-theme` choice while the admin has none, and (3) exposes a small sync hook
// so an explicit admin choice is mirrored to the public key. Storage failures are
// tolerated: the theme still works for the page view.
(function () {
  var ADMIN_KEY = "adminTheme";
  var PUBLIC_KEY = "mobofirm-theme";
  var root = document.documentElement;
  var lastChoice = null;

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      // Blocked storage: the choice applies to this page view only.
    }
  }

  function remove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (error) {
      // Blocked storage: nothing to clean up.
    }
  }

  function explicit(value) {
    return value === "dark" || value === "light" ? value : null;
  }

  function storedAdminChoice() {
    var raw = read(ADMIN_KEY);
    if (raw === null) return null;
    try {
      var value = JSON.parse(raw);
      return value === "light" || value === "dark" || value === "auto" ? value : null;
    } catch (error) {
      return null;
    }
  }

  var choice = storedAdminChoice();
  var publicChoice = explicit(read(PUBLIC_KEY));
  if ((choice === null || choice === "auto") && publicChoice !== null) {
    choice = publicChoice;
    write(ADMIN_KEY, JSON.stringify(choice));
  }
  choice = choice || "auto";
  lastChoice = choice;

  var scheme =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  var prefersDark = scheme !== null && scheme.matches;
  if (choice === "dark" || (choice === "auto" && prefersDark)) {
    root.classList.add("dark");
  } else if (choice === "light") {
    root.classList.add("light");
  }

  // Alpine's class binding only removes classes it added itself, so hand the classes
  // over right before it starts (same task, no paint in between); otherwise switching
  // from the pre-paint "dark" to "light" would leave both classes on <html>.
  document.addEventListener("alpine:init", function () {
    root.classList.remove("dark", "light");
  });

  // Unfold evaluates the system preference only when the choice changes, so in
  // "System" mode re-trigger its binding when the OS scheme flips.
  if (scheme !== null && typeof scheme.addEventListener === "function") {
    scheme.addEventListener("change", function () {
      var state = window.Alpine ? window.Alpine.$data(root) : null;
      if (state && state.adminTheme === "auto") {
        state.adminTheme = "light";
        state.adminTheme = "auto";
      }
    });
  }

  // Called by the header switcher (x-effect) whenever the Alpine choice changes. The
  // first call only records the initial state so merely opening the admin never
  // overwrites the public preference.
  window.mobofirmAdminTheme = {
    sync: function (next) {
      if (next === lastChoice) return;
      lastChoice = next;
      var chosen = explicit(next);
      if (chosen !== null) {
        write(PUBLIC_KEY, chosen);
      } else {
        remove(PUBLIC_KEY);
      }
    },
  };
})();
