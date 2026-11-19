// The ONE place Bootstrap's JavaScript enters the console bundle (CSS lives in
// styles/_bootstrap.scss). Bumping to Bootstrap 6 means editing this file and the shared
// templates/console/components/ only. Bootstrap's data-api (data-bs-toggle=
// "collapse" | "tooltip") registers itself on import and works for HTMX-swapped markup.
import Collapse from "bootstrap/js/src/collapse.js";
import Tooltip from "bootstrap/js/src/tooltip.js";

// Tooltips are opt-in markup (data-bs-toggle="tooltip"); create them lazily on first
// hover/focus so thousands of table cells cost nothing and swapped content just works.
function wireTooltips() {
  const create = (event) => {
    const target = event.target instanceof Element ? event.target.closest('[data-bs-toggle="tooltip"]') : null;
    if (target) Tooltip.getOrCreateInstance(target, { trigger: "hover focus" }).show();
  };
  document.addEventListener("pointerover", create);
  document.addEventListener("focusin", create);
}

wireTooltips();

export { Collapse, Tooltip };
