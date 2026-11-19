// Changelist behaviour (#console-changelist / [data-console-changelist]).
//
// Hooks inside the root:
//   input[data-console-row-select]  (Django's input.action-select[name=_selected_action])
//   input[data-console-select-all]            header "select all on this page" (tri-state)
//   [data-console-selected-count]             text: number selected
//   [data-console-bulkbar]                    `hidden` while nothing is selected
//   [data-console-select-across]              link/button: select every row of the whole result set
//   input[name=select_across]                 Django's hidden flag, set to 1 by the link above
//   [data-console-density="compact|comfortable"] buttons -> root[data-density], persisted
//   [data-console-search-clear]               clears the search box and re-submits (htmx)
//   [data-ui-sheet-toggle="<id>"] + #<id>[data-ui-sheet]   filter bar bottom sheet (js/ui/popover.js)
// Shift-click selects a range. The root survives htmx swaps of its results region; the counts
// are recomputed after every swap inside it. Rows get `data-selected="true"` and `.selected`.
import { EVENTS, announce, emit, storage } from "../lib/util.js";

const DENSITY_KEY = "mobofirm-console-density";

export function attachChangelist(root) {
  const controller = new AbortController();
  const { signal } = controller;
  let lastChecked = null;
  let across = false;

  const rows = () => [...root.querySelectorAll("input[data-console-row-select]")];
  const acrossFlag = () => root.querySelector("input[name=select_across]");

  // "Run" stays disabled (but readable, with its reason in the title) until an action is chosen.
  function syncRun() {
    for (const bar of root.querySelectorAll("[data-console-bulkbar]")) {
      const select = bar.querySelector("select[name=action]");
      const run = bar.querySelector("button[type=submit]");
      if (!(select instanceof HTMLSelectElement) || !(run instanceof HTMLButtonElement)) continue;
      const missing = select.value === "";
      run.disabled = missing;
      if (missing) run.title = select.options[0]?.text ? `Choose an action first` : "";
      else run.removeAttribute("title");
    }
  }

  root.addEventListener("change", (event) => {
    if (event.target instanceof HTMLSelectElement && event.target.name === "action") syncRun();
  }, { signal });

  function update(announceChange = false) {
    const boxes = rows();
    const selected = boxes.filter((box) => box.checked);
    for (const box of boxes) {
      const row = box.closest("tr");
      row?.classList.toggle("selected", box.checked);
      row?.toggleAttribute("data-selected", box.checked);
    }
    for (const all of root.querySelectorAll("input[data-console-select-all]")) {
      all.checked = boxes.length > 0 && selected.length === boxes.length;
      all.indeterminate = selected.length > 0 && selected.length < boxes.length;
    }
    for (const counter of root.querySelectorAll("[data-console-selected-count]")) {
      counter.textContent = String(selected.length);
    }
    for (const bar of root.querySelectorAll("[data-console-bulkbar]")) bar.hidden = selected.length === 0;
    syncRun();
    if (selected.length === 0 || selected.length < boxes.length) across = false;
    const flag = acrossFlag();
    if (flag) flag.value = across ? "1" : "0";
    root.toggleAttribute("data-select-across", across);
    root.dataset.selectedCount = String(selected.length);
    emit(EVENTS.selectionChange, { count: selected.length, total: boxes.length, across }, root);
    if (announceChange) announce(`${selected.length} of ${boxes.length} selected`);
  }

  function setAll(checked) {
    for (const box of rows()) box.checked = checked;
    lastChecked = null;
    update(true);
  }

  root.addEventListener(
    "click",
    (event) => {
      const box = event.target instanceof Element ? event.target.closest("input[data-console-row-select]") : null;
      if (!(box instanceof HTMLInputElement)) return;
      if (event.shiftKey && lastChecked && lastChecked !== box) {
        const boxes = rows();
        const [from, to] = [boxes.indexOf(lastChecked), boxes.indexOf(box)].sort((a, b) => a - b);
        for (const between of boxes.slice(from, to + 1)) between.checked = box.checked;
      }
      lastChecked = box;
    },
    { signal },
  );

  root.addEventListener(
    "change",
    (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      if (target.matches("input[data-console-select-all]")) setAll(target.checked);
      else if (target.matches("input[data-console-row-select]")) update(true);
    },
    { signal },
  );

  root.addEventListener(
    "click",
    (event) => {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest("[data-console-select-across]")) {
        event.preventDefault();
        across = true;
        update();
        announce("All results selected");
        return;
      }
      if (event.target.closest("[data-console-clear-selection]")) {
        event.preventDefault();
        across = false;
        setAll(false);
        return;
      }
      const density = event.target.closest("[data-console-density]");
      if (density instanceof HTMLElement) {
        setDensity(density.dataset.consoleDensity);
        return;
      }
      const clear = event.target.closest("[data-console-search-clear]");
      if (clear) {
        event.preventDefault();
        const form = clear.closest("form");
        const input = form?.querySelector('input[type="search"], input[name="q"]');
        if (input instanceof HTMLInputElement) {
          input.value = "";
          input.focus();
          form?.requestSubmit();
        }
        return;
      }
    },
    { signal },
  );

  function setDensity(value) {
    const density = value === "compact" ? "compact" : "comfortable";
    root.dataset.density = density;
    storage.set(DENSITY_KEY, density);
    for (const button of root.querySelectorAll("[data-console-density]")) {
      button.setAttribute("aria-pressed", String(button.dataset.consoleDensity === density));
    }
    emit(EVENTS.densityChange, { density }, root);
  }

  // Results are swapped inside the root: refresh counts and keep the stored density.
  const onSwap = () => {
    lastChecked = null;
    update();
    setDensity(root.dataset.density ?? storage.get(DENSITY_KEY));
  };
  document.addEventListener("htmx:after:swap", onSwap, { signal });

  setDensity(storage.get(DENSITY_KEY) ?? root.dataset.density);
  update();

  return { destroy: () => controller.abort() };
}
