// Catalogue list pages: removable filter chips and the variant filter of a model page.
// Both are progressive enhancements; the server renders every list and filter state.

function controlsOf(form) {
  return Array.from(form.elements).filter(
    (element) =>
      (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) &&
      element.name &&
      element.type !== "hidden" &&
      element.type !== "submit",
  );
}

// Reset a control to its "no filter" state and let the form's own HTMX trigger
// (change) reload the results, so chips, URL and list stay in sync with the server.
function clearControl(control) {
  if (control instanceof HTMLSelectElement) {
    control.selectedIndex = 0;
  } else {
    control.value = "";
  }
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function onFilterClick(event) {
  const trigger = event.target instanceof Element ? event.target.closest("[data-filter-clear],[data-filter-reset]") : null;
  if (!trigger) return;
  const form = document.getElementById(trigger.getAttribute("data-filter-form") || "");
  if (!(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  const controls = controlsOf(form);
  const name = trigger.getAttribute("data-filter-clear");
  const targets = name ? controls.filter((control) => control.name === name) : controls;
  targets.forEach((control, index) => {
    if (index === targets.length - 1) {
      clearControl(control);
    } else if (control instanceof HTMLSelectElement) {
      control.selectedIndex = 0;
    } else {
      control.value = "";
    }
  });
  form.querySelector("[data-live-search]")?.focus();
}

function applyVariant(scope, value) {
  scope.querySelectorAll("[data-variant-filter]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.getAttribute("data-variant-filter") === value));
  });
  let visible = 0;
  scope.querySelectorAll("[data-variant]").forEach((card) => {
    const show = value === "" || card.getAttribute("data-variant") === value;
    card.hidden = !show;
    if (show) visible += 1;
  });
  scope.querySelectorAll("[data-variant-group]").forEach((group) => {
    group.hidden = !group.querySelector("[data-variant]:not([hidden])");
  });
  const empty = scope.querySelector("[data-variant-empty]");
  if (empty) empty.hidden = visible > 0;
}

function onVariantClick(event) {
  const button = event.target instanceof Element ? event.target.closest("[data-variant-filter]") : null;
  const scope = button?.closest("[data-variant-scope]");
  if (!button || !scope) return;
  applyVariant(scope, button.getAttribute("data-variant-filter") || "");
}

export function initializeCatalogFilters() {
  document.addEventListener("click", onFilterClick);
  document.addEventListener("click", onVariantClick);
}
