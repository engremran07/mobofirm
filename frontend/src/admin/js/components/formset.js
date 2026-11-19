// Inline formsets ([data-console-formset]): add, remove/undo, reorder rows.
//
// Markup (templates/admin/edit_inline/*):
//   root [data-console-formset] containing Django's management form (<prefix>-TOTAL_FORMS ...)
//   rows        [data-console-formset-row] or .console-inline__row  (inside [data-console-formset-rows]
//               when present, otherwise siblings)
//   template    <template data-console-formset-template> with `__prefix__` in names/ids
//   controls    [data-console-formset-add]  [data-console-formset-remove]
//               [data-console-formset-move="up|down"]
//   ordering    input[data-console-formset-order] or input[name$="-ORDER"] (rewritten 1..n)
//   empty hint  [data-console-formset-empty] (shown only while there are no visible rows)
// Existing rows (those with an id input / DELETE checkbox) are toggled for deletion
// (row[data-deleted], DELETE ticked; activate again to undo). New rows are removed from the DOM
// and later new rows are renumbered, so TOTAL_FORMS stays contiguous. MAX_NUM_FORMS disables
// "add". Fires `console-formset-added|removed|moved` (bubbling, detail {row, prefix}).
import { renderIcons } from "../icons.js";
import { EVENTS, announce, emit } from "../lib/util.js";

const ROW = "[data-console-formset-row], .console-inline__row";

function replaceInTree(node, pattern, value) {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  for (let current = walker.currentNode; current; current = walker.nextNode()) {
    if (current.nodeType === Node.TEXT_NODE) {
      current.nodeValue = current.nodeValue.replace(pattern, value);
    } else if (current.nodeType === Node.ELEMENT_NODE) {
      for (const attribute of [...current.attributes]) {
        if (pattern.test(attribute.value)) current.setAttribute(attribute.name, attribute.value.replace(pattern, value));
        pattern.lastIndex = 0;
      }
    }
  }
}

export function attachFormset(root) {
  const controller = new AbortController();
  const { signal } = controller;
  const total = root.querySelector('input[name$="-TOTAL_FORMS"]');
  const initial = root.querySelector('input[name$="-INITIAL_FORMS"]');
  const max = root.querySelector('input[name$="-MAX_NUM_FORMS"]');
  if (!(total instanceof HTMLInputElement)) return { destroy() {} };
  const prefix = total.name.replace(/-TOTAL_FORMS$/, "");
  const container = root.querySelector("[data-console-formset-rows]") ?? root;
  const template = root.querySelector("template[data-console-formset-template]");
  const rows = () => [...container.querySelectorAll(ROW)].filter((row) => row.closest("[data-console-formset]") === root);
  const initialCount = () => Number(initial?.value ?? 0);
  const isExisting = (row) => rows().indexOf(row) < initialCount();
  const visibleRows = () => rows().filter((row) => !row.hasAttribute("data-deleted"));
  const orderInput = (row) => row.querySelector("input[data-console-formset-order], input[name$='-ORDER']");

  function refresh() {
    total.value = String(rows().length);
    const limit = Number(max?.value);
    const full = Number.isFinite(limit) && limit > 0 && limit < 1000 && visibleRows().length >= limit;
    for (const add of root.querySelectorAll("[data-console-formset-add]")) {
      add.toggleAttribute("disabled", full);
      add.setAttribute("aria-disabled", String(full));
    }
    for (const empty of root.querySelectorAll("[data-console-formset-empty]")) empty.hidden = visibleRows().length > 0;
    visibleRows().forEach((row, index) => {
      const input = orderInput(row);
      if (input) input.value = String(index + 1);
      const up = row.querySelector('[data-console-formset-move="up"]');
      const down = row.querySelector('[data-console-formset-move="down"]');
      if (up) up.toggleAttribute("disabled", index === 0);
      if (down) down.toggleAttribute("disabled", index === visibleRows().length - 1);
    });
  }

  function add() {
    if (!template || root.querySelector("[data-console-formset-add][disabled]")) return;
    const index = rows().length;
    const clone = template.content.cloneNode(true);
    replaceInTree(clone, /__prefix__/g, String(index));
    const row = clone.querySelector(ROW) ?? clone.firstElementChild;
    const last = rows().at(-1);
    if (last) last.after(clone);
    else (template.parentElement === container ? template : container.lastElementChild ?? template).before(clone);
    if (!(row instanceof HTMLElement)) return;
    renderIcons(row);
    refresh();
    row.querySelector("input:not([type=hidden]), select, textarea")?.focus({ preventScroll: false });
    emit(EVENTS.formsetAdded, { row, prefix, index }, row);
    announce(`Row ${visibleRows().length} added`);
  }

  function renumber(from) {
    const all = rows();
    for (let index = from; index < all.length; index += 1) {
      const pattern = new RegExp(`${prefix}-\\d+-`, "g");
      replaceInTree(all[index], pattern, `${prefix}-${index}-`);
    }
  }

  function remove(row) {
    const checkbox = row.querySelector('input[type=checkbox][name$="-DELETE"]');
    if (isExisting(row) && checkbox instanceof HTMLInputElement) {
      const deleted = !row.hasAttribute("data-deleted");
      row.toggleAttribute("data-deleted", deleted);
      checkbox.checked = deleted;
      for (const button of row.querySelectorAll("[data-console-formset-remove]")) {
        button.setAttribute("aria-pressed", String(deleted));
      }
      announce(deleted ? "Row marked for deletion" : "Row restored");
    } else {
      const index = rows().indexOf(row);
      const next = rows()[index + 1] ?? rows()[index - 1];
      row.remove();
      renumber(index);
      announce("Row removed");
      next?.querySelector("[data-console-formset-remove], input:not([type=hidden])")?.focus();
    }
    refresh();
    emit(EVENTS.formsetRemoved, { row, prefix }, container);
  }

  function move(row, direction) {
    const siblings = visibleRows();
    const index = siblings.indexOf(row);
    const other = siblings[index + (direction === "up" ? -1 : 1)];
    if (!other) return;
    if (direction === "up") other.before(row);
    else other.after(row);
    refresh();
    row.querySelector(`[data-console-formset-move="${direction}"]:not([disabled])`)?.focus();
    announce(`Moved ${direction}. Position ${visibleRows().indexOf(row) + 1} of ${visibleRows().length}`);
    emit(EVENTS.formsetMoved, { row, prefix }, row);
  }

  root.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const addButton = event.target.closest("[data-console-formset-add]");
    if (addButton && root.contains(addButton)) {
      event.preventDefault();
      add();
      return;
    }
    const removeButton = event.target.closest("[data-console-formset-remove]");
    const moveButton = event.target.closest("[data-console-formset-move]");
    const control = removeButton ?? moveButton;
    const row = control?.closest(ROW);
    if (!control || !row || row.closest("[data-console-formset]") !== root) return;
    event.preventDefault();
    if (removeButton) remove(row);
    else move(row, moveButton.dataset.consoleFormsetMove);
  }, { signal });

  refresh();
  return { destroy: () => controller.abort() };
}
