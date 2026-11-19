// Headless-style switch, radio group, select (listbox) and native dialog / offcanvas primitives.
// Same data-attribute convention as popover.js: data-open / data-active / data-selected / data-checked / data-disabled / data-focus.
// Why plain delegated JavaScript instead of Alpine.data components: every one of these is server-rendered markup that HTMX
// swaps in and out; document-level delegation keeps working for swapped fragments with no per-fragment x-data bootstrap,
// no expression evaluation (strict CSP) and no re-initialisation. Appearance is CSS only (shared tokens).
//
// Switch        <input type="checkbox" role="switch" data-ui-switch>            native checkbox is the value; data-checked mirrors it
// Radio group   <div role="radiogroup" data-ui-radiogroup><button role="radio" aria-checked data-value tabindex>..</button>
//               <input type="hidden" data-ui-radiogroup-input name="x"></div>   roving tabindex, Arrow/Home/End select, Space/Enter select
// Select        <div data-ui-select data-ui-popover><select class="c-select__native">..</select><button data-ui-trigger>..</button>
//               <div data-ui-panel><ul role="listbox"><li role="presentation"><button role="option" data-value>..</button></li></ul></div></div>
//               the native <select> stays the submitted value and the no-JS fallback (hidden by CSS only when scripting is on)
// Dialog        <dialog data-ui-dialog [data-ui-dialog="offcanvas"]> opened with [data-ui-dialog-open="id"], closed with [data-ui-dialog-close];
//               the browser provides the focus trap and Escape; this module restores focus, sets data-open and closes on backdrop click.
import { closePopover } from "./popover.js";

const emitChange = (element) => element.dispatchEvent(new Event("change", { bubbles: true }));

// ---- Switch --------------------------------------------------------------------------------
function syncSwitch(input) {
  input.toggleAttribute("data-checked", input.checked);
  input.toggleAttribute("data-disabled", input.disabled);
}

export function scanSwitches(root = document) {
  for (const input of root.querySelectorAll("input[data-ui-switch]")) syncSwitch(input);
}

// ---- Radio group ---------------------------------------------------------------------------
const radios = (group) => [...group.querySelectorAll('[role="radio"]:not([data-disabled])')];

function selectRadio(group, radio, focus = true) {
  for (const other of group.querySelectorAll('[role="radio"]')) {
    const on = other === radio;
    other.setAttribute("aria-checked", String(on));
    other.toggleAttribute("data-selected", on);
    other.tabIndex = on ? 0 : -1;
  }
  const input = group.querySelector("[data-ui-radiogroup-input]");
  if (input instanceof HTMLInputElement && input.value !== (radio.dataset.value ?? "")) {
    input.value = radio.dataset.value ?? "";
    emitChange(input);
  }
  if (focus) radio.focus();
}

export function scanRadioGroups(root = document) {
  for (const group of root.querySelectorAll("[data-ui-radiogroup]")) {
    syncNativeRadios(group);
    const all = radios(group);
    if (all.length === 0) continue;
    const current = all.find((radio) => radio.getAttribute("aria-checked") === "true") ?? all[0];
    for (const radio of group.querySelectorAll('[role="radio"]')) {
      radio.toggleAttribute("data-selected", radio === current);
      radio.tabIndex = radio === current ? 0 : -1;
    }
  }
}

// Native radios inside a group: the checked option's label carries data-selected.
function syncNativeRadios(group) {
  for (const input of group.querySelectorAll('input[type="radio"]')) {
    input.labels?.[0]?.toggleAttribute("data-selected", input.checked);
    input.labels?.[0]?.toggleAttribute("data-disabled", input.disabled);
  }
}

// ---- Select (listbox over a native <select>) ------------------------------------------------
function syncSelect(select) {
  const root = select.closest("[data-ui-select]");
  if (!(root instanceof HTMLElement)) return;
  const label = root.querySelector("[data-ui-select-value]");
  for (const option of root.querySelectorAll('[role="option"]')) {
    const on = option.dataset.value === select.value;
    option.setAttribute("aria-selected", String(on));
    option.toggleAttribute("data-selected", on);
    if (on && label) label.textContent = option.textContent.trim();
  }
  root.toggleAttribute("data-empty", select.value === "");
}

export function scanSelects(root = document) {
  for (const select of root.querySelectorAll("[data-ui-select] > select")) {
    // The visible control is the button; keep the native select as the value, out of the tab order and the accessibility tree.
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    syncSelect(select);
  }
}

function pickOption(option) {
  const root = option.closest("[data-ui-select]");
  const select = root?.querySelector("select");
  if (!(root instanceof HTMLElement) || !(select instanceof HTMLSelectElement)) return;
  select.value = option.dataset.value ?? "";
  syncSelect(select);
  emitChange(select);
}

// ---- Dialog / offcanvas --------------------------------------------------------------------
const openers = new WeakMap();

export function openUiDialog(dialog, opener = document.activeElement) {
  if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
  openers.set(dialog, opener instanceof HTMLElement ? opener : null);
  dialog.showModal();
  dialog.setAttribute("data-open", "");
}

export function closeUiDialog(dialog) {
  if (dialog instanceof HTMLDialogElement && dialog.open) dialog.close();
}

function onDialogClose(event) {
  const dialog = event.target;
  if (!(dialog instanceof HTMLDialogElement) || !dialog.hasAttribute("data-ui-dialog")) return;
  dialog.removeAttribute("data-open");
  openers.get(dialog)?.focus();
}

// ---- Wiring --------------------------------------------------------------------------------
function onClick(event) {
  if (!(event.target instanceof Element)) return;
  const radio = event.target.closest('[role="radio"]');
  const group = radio?.closest("[data-ui-radiogroup]");
  if (radio && group && !radio.hasAttribute("data-disabled")) {
    selectRadio(group, radio);
    return;
  }
  const option = event.target.closest("[data-ui-select] [role='option']");
  if (option instanceof HTMLElement) {
    pickOption(option);
    const root = option.closest("[data-ui-popover]");
    if (root) closePopover(root);
    return;
  }
  const open = event.target.closest("[data-ui-dialog-open]");
  if (open instanceof HTMLElement) {
    openUiDialog(document.getElementById(open.dataset.uiDialogOpen ?? ""), open);
    return;
  }
  if (event.target.closest("[data-ui-dialog-close]")) {
    closeUiDialog(event.target.closest("dialog"));
    return;
  }
  const target = event.target;
  if (target instanceof HTMLDialogElement && target.hasAttribute("data-ui-dialog")) {
    // A click on the backdrop lands on the dialog element itself, outside its content box.
    const box = target.getBoundingClientRect();
    const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    if (!inside) closeUiDialog(target);
  }
}

function onKeydown(event) {
  const radio = event.target instanceof Element ? event.target.closest('[role="radio"]') : null;
  const group = radio?.closest("[data-ui-radiogroup]");
  if (!radio || !group) return;
  const list = radios(group);
  const index = list.indexOf(radio);
  const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
  let next = null;
  if (event.key in moves) next = list[(index + moves[event.key] + list.length) % list.length];
  else if (event.key === "Home") next = list[0];
  else if (event.key === "End") next = list.at(-1);
  else if (event.key === " " || event.key === "Enter") next = radio;
  if (!next) return;
  event.preventDefault();
  selectRadio(group, next);
}

function onChange(event) {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.matches("[data-ui-switch]")) syncSwitch(target);
  if (target instanceof HTMLInputElement && target.type === "radio") {
    const group = target.closest("[data-ui-radiogroup]");
    if (group) syncNativeRadios(group);
  }
  if (target instanceof HTMLSelectElement && target.closest("[data-ui-select]")) syncSelect(target);
}

function onFocus(event) {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.matches("[data-ui-switch]")) target.toggleAttribute("data-focus", event.type === "focusin");
}

export function initControls() {
  scanSwitches();
  scanRadioGroups();
  scanSelects();
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("change", onChange);
  document.addEventListener("focusin", onFocus);
  document.addEventListener("focusout", onFocus);
  document.addEventListener("close", onDialogClose, true);
  document.addEventListener("htmx:after:swap", () => {
    scanSwitches();
    scanRadioGroups();
    scanSelects();
  });
}
