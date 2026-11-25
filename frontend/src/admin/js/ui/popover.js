// Headless-style popover / listbox / sheet primitives for the staff console (no framework, no eval).
//
// The convention is Headless UI's: behaviour and accessibility live here, appearance lives in CSS that
// reads state from data attributes. Nothing in this file sets a colour or a size.
//
//   <div data-ui-popover>                                  root; gets data-open while the panel is shown
//     <button data-ui-trigger aria-haspopup="listbox|menu|dialog" aria-expanded aria-controls="panel-id">
//     <div data-ui-panel id="panel-id" hidden>
//       <ul role="listbox|menu"> <a role="option|menuitem" aria-selected data-selected data-disabled>   items (links or buttons)
//
// State attributes written here: data-open (root), data-active (the item that has focus/pointer), data-focus (keyboard focus
// inside the root), plus aria-expanded on the trigger. Items may carry data-selected / data-disabled from the server.
// Keyboard (WAI-ARIA listbox / menu button patterns): Enter, Space and ArrowDown/ArrowUp open the panel; ArrowUp/ArrowDown,
// Home and End move between items; typing jumps to the next item starting with the typed text; Escape closes and returns focus
// to the trigger; Tab and outside clicks close without stealing focus.
//
// Fixed panels are portalled to the body before viewport positioning so transformed, filtered or
// scrolling ancestors cannot change their coordinate system or clip the panel.
//
// Sheets: [data-ui-sheet-toggle="<id>"] toggles data-open on #<id> (the filter bar becomes a bottom sheet below 768px).
const ITEM = '[role="option"],[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[data-ui-item]';
const FOCUSABLE = 'input:not([type="hidden"]):not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled]),a[href]';
const TYPEAHEAD_MS = 600;

let typed = "";
let typedTimer = 0;
let generatedId = 0;
const panelOwners = new WeakMap();
const rootPanels = new WeakMap();

const items = (panel) =>
  [...panel.querySelectorAll(ITEM)].filter((item) => !item.hasAttribute("data-disabled") && item.getAttribute("aria-disabled") !== "true");
const rootOf = (node) => {
  if (!(node instanceof Element)) return null;
  const root = node.closest("[data-ui-popover]");
  if (root) return root;
  const panel = node.closest("[data-ui-panel]");
  return panel ? panelOwners.get(panel) ?? null : null;
};
const trigger = (root) => root.querySelector("[data-ui-trigger]");
const panelOf = (root) => rootPanels.get(root) ?? root.querySelector("[data-ui-panel]");
const contains = (root, node) => root.contains(node) || Boolean(panelOf(root)?.contains(node));
const isOpen = (root) => root.hasAttribute("data-open");

export const popoverRoot = rootOf;
export const popoverPanel = panelOf;

function setActive(root, item) {
  for (const other of root.querySelectorAll("[data-active]")) other.removeAttribute("data-active");
  item?.setAttribute("data-active", "");
}

function uniqueId(prefix) {
  let id;
  do {
    generatedId += 1;
    id = `${prefix}-${generatedId}`;
  } while (document.getElementById(id));
  return id;
}

function associate(panel, button) {
  if (!panel.id) panel.id = uniqueId("console-popover-panel");
  button.setAttribute("aria-controls", panel.id);
  if (panel.getAttribute("role") === "menu") {
    if (!button.id) button.id = uniqueId("console-popover-trigger");
    if (!panel.hasAttribute("aria-label") && !panel.hasAttribute("aria-labelledby")) {
      panel.setAttribute("aria-labelledby", button.id);
    }
  }
}

function place(root) {
  const panel = panelOf(root);
  root.removeAttribute("data-align");
  if (!panel) return;
  if (panel.hasAttribute("data-ui-fixed")) {
    // Menus inside scrolling table wrappers must not be clipped: position against the viewport.
    const anchor = trigger(root)?.getBoundingClientRect();
    if (!anchor) return;
    const margin = 8;
    const gap = 6;
    panel.style.position = "fixed";
    panel.style.zIndex = "1040";
    panel.style.maxWidth = `${Math.max(0, window.innerWidth - margin * 2)}px`;
    panel.style.maxHeight = `${Math.max(0, window.innerHeight - margin * 2)}px`;
    panel.style.overflowY = "auto";
    const end = panel.classList.contains("dropdown-menu-end");
    panel.style.right = "auto";
    const initialBox = panel.getBoundingClientRect();
    const maxLeft = Math.max(margin, window.innerWidth - initialBox.width - margin);
    const preferredLeft = end ? anchor.right - initialBox.width : anchor.left;
    panel.style.left = `${Math.max(margin, Math.min(preferredLeft, maxLeft))}px`;

    const below = Math.max(0, window.innerHeight - anchor.bottom - margin - gap);
    const above = Math.max(0, anchor.top - margin - gap);
    let side = below >= above ? "bottom" : "top";
    const available = (side === "bottom" ? below : above);
    panel.style.maxHeight = `${Math.max(0, available)}px`;
    let fittedHeight = panel.getBoundingClientRect().height;
    let top =
      side === "bottom"
        ? anchor.bottom + gap
        : anchor.top - gap - fittedHeight;
    top = Math.max(margin, Math.min(top, window.innerHeight - fittedHeight - margin));
    panel.style.top = `${top}px`;
      if (side === "bottom") {
        const bottomEdge = top + fittedHeight;
        if (bottomEdge > anchor.top && top < anchor.bottom) {
          top = Math.max(margin, anchor.top - fittedHeight - gap);
          panel.style.top = `${top}px`;
        }
      } else {
        if (top + fittedHeight > anchor.top && top < anchor.bottom) {
          top = anchor.bottom + gap;
          top = Math.max(margin, Math.min(top, window.innerHeight - fittedHeight - margin));
          panel.style.top = `${top}px`;
        }
      }
    root.setAttribute("data-side", side);
    return;
  }
  if (panel.getBoundingClientRect().right > window.innerWidth - 8) root.setAttribute("data-align", "end");
}

// Menu items are ordinary links and buttons in the markup; give them their ARIA role and the shared data attributes.
function prepare(panel) {
  if (panel.getAttribute("role") !== "menu") return;
  for (const item of panel.querySelectorAll(".dropdown-item, [data-console-theme-set]")) {
    if (!item.hasAttribute("role")) item.setAttribute("role", "menuitem");
    item.setAttribute("data-ui-item", "");
    if (item.matches("[data-console-theme-set]")) item.setAttribute("data-ui-keep-open", "");
    item.tabIndex = -1;
    if (item.matches(".disabled, :disabled, [aria-disabled='true']")) item.setAttribute("data-disabled", "");
    if (item.matches("[aria-current], [aria-pressed='true']")) item.setAttribute("data-selected", "");
  }
}

function clearPlacement(panel, root) {
  for (const property of [
    "position",
    "zIndex",
    "top",
    "insetBlockStart",
    "insetInlineStart",
    "insetInlineEnd",
    "left",
    "right",
    "maxWidth",
    "maxHeight",
    "overflowY",
  ]) {
    panel.style[property] = "";
  }
  root.removeAttribute("data-side");
}

export function openPopover(root, focus = "auto") {
  const panel = panelOf(root);
  const button = trigger(root);
  if (!panel || !button || isOpen(root)) return;
  for (const other of document.querySelectorAll("[data-ui-popover][data-open]")) closePopover(other, false);
  associate(panel, button);
  panel.hidden = false;
  panel.toggleAttribute("data-ui-open", true);
  if (panel.hasAttribute("data-ui-fixed")) {
    panelOwners.set(panel, root);
    rootPanels.set(root, panel);
    document.body.append(panel);
  }
  prepare(panel);
  root.setAttribute("data-open", "");
  button.setAttribute("aria-expanded", "true");
  place(root);
  const list = items(panel);
  const target =
    focus === "last"
      ? list.at(-1)
      : (list.find((item) => item.hasAttribute("data-selected")) ?? list[0] ?? panel.querySelector(FOCUSABLE));
  if (!target) return;
  target.focus({ preventScroll: true });
  if (panel.hasAttribute("data-ui-fixed")) {
    const panelRect = panel.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    if (targetRect.top < panelRect.top) panel.scrollTop -= panelRect.top - targetRect.top;
    else if (targetRect.bottom > panelRect.bottom) panel.scrollTop += targetRect.bottom - panelRect.bottom;
  }
}

export function closePopover(root, restoreFocus = true) {
  const panel = panelOf(root);
  const button = trigger(root);
  if (!panel || !isOpen(root)) return;
  panel.hidden = true;
  panel.removeAttribute("data-ui-open");
  clearPlacement(panel, root);
  if (panel.hasAttribute("data-ui-fixed") && panel.parentElement !== root) {
    root.append(panel);
  }
  root.removeAttribute("data-open");
  root.removeAttribute("data-focus");
  setActive(root, null);
  button?.setAttribute("aria-expanded", "false");
  if (restoreFocus) button?.focus();
}

function move(root, current, step) {
  const list = items(panelOf(root));
  if (!list.length) return;
  const index = list.indexOf(current);
  const next = step === "first" ? 0 : step === "last" ? list.length - 1 : (index + step + list.length) % list.length;
  list[next].focus();
}

function typeAhead(root, key) {
  window.clearTimeout(typedTimer);
  typed += key.toLowerCase();
  typedTimer = window.setTimeout(() => {
    typed = "";
  }, TYPEAHEAD_MS);
  const list = items(panelOf(root));
  const start = list.indexOf(document.activeElement) + 1;
  const ordered = [...list.slice(start), ...list.slice(0, start)];
  ordered.find((item) => item.textContent.trim().toLowerCase().startsWith(typed))?.focus();
}

function onKeydown(event) {
  const root = rootOf(event.target);
  if (!root) return;
  const button = trigger(root);
  const open = isOpen(root);
  root.setAttribute("data-focus", "");
  if (event.target === button && !open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    openPopover(root, event.key === "ArrowUp" ? "last" : "auto");
    return;
  }
  if (!open) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closePopover(root);
    return;
  }
  if (event.key === "Tab") {
    closePopover(root, false);
    return;
  }
  const item = event.target instanceof Element ? event.target.closest(ITEM) : null;
  if (!item && event.target !== button) return;
  const keys = { ArrowDown: 1, ArrowUp: -1, Home: "first", End: "last" };
  if (event.key in keys) {
    event.preventDefault();
    move(root, item, keys[event.key]);
  } else if (item && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (event.key === " " && typed === "") {
      if (!(item instanceof HTMLButtonElement)) {
        event.preventDefault();
        item.click();
      }
      return;
    }
    typeAhead(root, event.key);
  }
}

function toggleSheet(button) {
  const sheet = document.getElementById(button.dataset.uiSheetToggle ?? "");
  if (!sheet) return;
  const open = !sheet.hasAttribute("data-open");
  sheet.toggleAttribute("data-open", open);
  for (const toggle of document.querySelectorAll(`[data-ui-sheet-toggle="${CSS.escape(sheet.id)}"]`)) {
    toggle.setAttribute("aria-expanded", String(open));
  }
  if (open) sheet.querySelector(FOCUSABLE)?.focus();
}

function onClick(event) {
  if (!(event.target instanceof Element)) return;
  const toggle = event.target.closest("[data-ui-trigger]");
  if (toggle) {
    const root = rootOf(toggle);
    if (root) isOpen(root) ? closePopover(root) : openPopover(root);
    return;
  }
  const item = event.target.closest(ITEM);
  const owner = item && !item.hasAttribute("data-ui-keep-open") && item.closest("[data-ui-panel]") ? rootOf(item) : null;
  if (owner) {
    closePopover(owner, owner.hasAttribute("data-ui-select"));
    return;
  }
  const sheetToggle = event.target.closest("[data-ui-sheet-toggle]");
  if (sheetToggle instanceof HTMLElement) {
    toggleSheet(sheetToggle);
    return;
  }
  for (const root of document.querySelectorAll("[data-ui-popover][data-open]")) {
    if (!contains(root, event.target)) closePopover(root, false);
  }
}

function onFocusin(event) {
  const root = rootOf(event.target);
  if (!root) return;
  root.setAttribute("data-focus", "");
  const item = event.target instanceof Element ? event.target.closest(ITEM) : null;
  if (item) setActive(root, item);
}

function onFocusout(event) {
  const root = rootOf(event.target);
  if (!root) return;
  const next = event.relatedTarget;
  if (!(next instanceof Node) || !contains(root, next)) {
    root.removeAttribute("data-focus");
    if (isOpen(root) && next) closePopover(root, false);
  }
}

function onPointerover(event) {
  const item = event.target instanceof Element ? event.target.closest(ITEM) : null;
  const root = item ? rootOf(item) : null;
  if (root && item) setActive(root, item);
}

function onSheetEscape(event) {
  if (event.key !== "Escape") return;
  const sheet = document.querySelector("[data-ui-sheet][data-open]");
  if (!(sheet instanceof HTMLElement) || sheet.querySelector("[data-ui-popover][data-open]")) return;
  toggleSheet(document.querySelector(`[data-ui-sheet-toggle="${CSS.escape(sheet.id)}"]`) ?? sheet);
  document.querySelector(`[data-ui-sheet-toggle="${CSS.escape(sheet.id)}"]`)?.focus();
}

export function initPopovers() {
  const closeFixed = () => {
    for (const root of document.querySelectorAll("[data-ui-popover][data-open]")) {
      if (!panelOf(root)?.hasAttribute("data-ui-fixed")) continue;
      if (document.activeElement instanceof Node && contains(root, document.activeElement)) {
        place(root);
        continue;
      }
      closePopover(root, false);
    }
  };
  window.addEventListener("resize", closeFixed);
  document.addEventListener("scroll", closeFixed, { capture: true, passive: true });
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("keydown", onSheetEscape);
  document.addEventListener("focusin", onFocusin);
  document.addEventListener("focusout", onFocusout);
  document.addEventListener("pointerover", onPointerover);
}
