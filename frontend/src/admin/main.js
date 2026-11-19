// Staff console entry: the single JavaScript bundle of the admin (hashed by Vite, resolved by
// {% console_assets %}). Order matters and is deliberate:
//   1. htmx 4 policy (CSRF, swap safety, confirm routing, toasts, focus)   js/htmx-setup.js
//   2. Bootstrap's data API (collapse | modal | tooltip; menus use js/ui/popover.js)   js/vendor-bootstrap.js
//   3. document-level behaviours (theme, sidebar, dialogs, palette, ...)    js/components/*
//   4. hook scanner for roots that need a controller (changelist, formset, tabs, combobox, ...)
//   5. Alpine CSP build, started after the DOM is ready: components are registered with
//      Alpine.data(); there are no inline expressions anywhere (strict CSP, no 'unsafe-eval').
// Rarely used widgets (editor, charts) must be lazy chunks via dynamic import(), never added here.
import Alpine from "@alpinejs/csp";

import { initBehaviours, scan } from "./js/behaviours.js";
import { attachChangelist } from "./js/components/changelist.js";
import { attachCombobox } from "./js/components/combobox.js";
import { confirmDialog, initConfirm } from "./js/components/confirm.js";
import { closeDialog, initDialogHost, openDialog } from "./js/components/dialog.js";
import { attachFormset } from "./js/components/formset.js";
import { initPalette, openPalette } from "./js/components/palette.js";
import { initRelated } from "./js/components/related.js";
import { initShortcuts } from "./js/components/shortcuts.js";
import { initSidebar, sidebarComponent } from "./js/components/sidebar.js";
import { attachTabs } from "./js/components/tabs.js";
import { initTheme, setTheme, themeComponent } from "./js/components/theme.js";
import { initToasts, showToast, toastsComponent } from "./js/components/toasts.js";
import { initUi } from "./js/components/ui.js";
import { initUnsavedGuard } from "./js/components/unsaved.js";
import { initWidgets } from "./js/components/widgets.js";
import { htmx, setupHtmx } from "./js/htmx-setup.js";
import { renderIcons } from "./js/icons.js";
import { initControls } from "./js/ui/controls.js";
import { initModals } from "./js/ui/modal.js";
import { initPopovers } from "./js/ui/popover.js";
import { behaviour } from "./js/lib/util.js";

window.htmx = htmx;
window.Alpine = Alpine;
// Small public surface for server-driven UI and tests; no business logic lives here.
window.mobofirmConsole = Object.freeze({
  toast: showToast,
  confirm: confirmDialog,
  openDialog,
  closeDialog,
  openPalette,
  setTheme,
  scan,
});

function registerAlpine() {
  Alpine.data("consoleSidebar", sidebarComponent);
  Alpine.data("consoleTheme", themeComponent);
  Alpine.data("consoleToasts", toastsComponent);
  Alpine.data("consoleChangelist", behaviour(attachChangelist));
  Alpine.data("consoleFormset", behaviour(attachFormset));
  Alpine.data("consoleTabs", behaviour(attachTabs));
  Alpine.data("consoleCombobox", behaviour(attachCombobox));
}

function boot() {
  setupHtmx();
  initTheme();
  initSidebar();
  initToasts();
  initConfirm();
  initDialogHost();
  initRelated();
  initUnsavedGuard();
  initPalette();
  initShortcuts();
  initUi();
  initPopovers();
  initControls();
  initModals();
  initWidgets();
  initBehaviours();
  renderIcons();
  registerAlpine();
  Alpine.start();
  document.documentElement.setAttribute("data-console-ready", "");
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
else boot();
