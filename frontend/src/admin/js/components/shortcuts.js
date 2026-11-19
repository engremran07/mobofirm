// Keyboard shortcuts (single keys never fire while typing, with a modifier, or over a dialog).
//   ?        this list                          /        focus the list search (or open the palette)
//   g d      go to the dashboard                [        collapse / expand the sidebar
//   Ctrl/Cmd+K  command palette (palette.js)
// Any link or button can declare its own: <a data-console-shortcut="g u" data-console-shortcut-label="Users">.
// A sequence is `g` followed by one key within a second.
import { iconPlaceholder, renderIcons } from "../icons.js";
import { NativeModal as Modal } from "../ui/modal.js";
import { isTypingTarget, nextId } from "../lib/util.js";
import { openPalette } from "./palette.js";
import { toggleSidebar } from "./sidebar.js";

const SEQUENCE_MS = 1000;
const BUILT_IN = [
  { keys: "?", label: "Show keyboard shortcuts" },
  { keys: "/", label: "Search this list, or open search" },
  { keys: "g d", label: "Go to the dashboard" },
  { keys: "[", label: "Collapse or expand the sidebar" },
  { keys: "Ctrl K", label: "Search and jump to anything" },
];

let pending = "";
let timer = 0;

const declared = () => [...document.querySelectorAll("[data-console-shortcut]")].filter((el) => el instanceof HTMLElement);

function activate(element) {
  if (element instanceof HTMLElement) element.click();
}

function focusSearch() {
  const field = document.querySelector('[role="search"] input[type="search"], #c-cl-q');
  if (field instanceof HTMLElement && field.getClientRects().length > 0) {
    field.focus();
    return true;
  }
  return false;
}

function showHelp() {
  const id = nextId("console-shortcuts");
  const root = document.createElement("dialog");
  root.className = "modal";
  root.setAttribute("aria-labelledby", `${id}-title`);
  const dialog = document.createElement("div");
  dialog.className = "modal-dialog modal-dialog-centered modal-dialog-scrollable";
  const content = document.createElement("div");
  content.className = "modal-content";

  const header = document.createElement("div");
  header.className = "modal-header";
  const title = document.createElement("h2");
  title.className = "modal-title";
  title.id = `${id}-title`;
  title.textContent = "Keyboard shortcuts";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "c-icon-btn";
  close.setAttribute("data-ui-dismiss", "modal");
  close.setAttribute("aria-label", "Close");
  const icon = iconPlaceholder("x");
  icon.className = "c-icon";
  close.append(icon);
  header.append(title, close);

  const body = document.createElement("div");
  body.className = "modal-body";
  const list = document.createElement("dl");
  list.className = "mb-0";
  const rows = [
    ...BUILT_IN,
    ...declared().map((element) => ({
      keys: element.dataset.consoleShortcut ?? "",
      label: element.dataset.consoleShortcutLabel ?? element.getAttribute("aria-label") ?? element.textContent.trim(),
    })),
  ];
  for (const row of rows) {
    const wrapper = document.createElement("div");
    wrapper.className = "d-flex justify-content-between gap-3 py-1";
    const term = document.createElement("dt");
    term.className = "fw-normal";
    term.textContent = row.label;
    const keys = document.createElement("dd");
    keys.className = "mb-0";
    for (const part of row.keys.split(" ")) {
      const key = document.createElement("kbd");
      key.className = "c-kbd me-1";
      key.textContent = part;
      keys.append(key);
    }
    wrapper.append(term, keys);
    list.append(wrapper);
  }
  body.append(list);
  content.append(header, body);
  dialog.append(content);
  root.append(dialog);
  document.body.append(root);
  renderIcons(root);
  const opener = document.activeElement;
  const modal = new Modal(root);
  root.addEventListener("hidden.ui.modal", () => {
    modal.dispose();
    root.remove();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
  });
  modal.show();
}

function reset() {
  pending = "";
  window.clearTimeout(timer);
}

function onKeydown(event) {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
  if (isTypingTarget(event.target) || document.querySelector(".modal.show")) {
    reset();
    return;
  }
  if (event.key.length !== 1) return;
  const key = event.key;
  const typed = pending ? `${pending} ${key}` : key;

  const custom = declared().find((element) => element.dataset.consoleShortcut === typed);
  let handled = true;
  if (custom) activate(custom);
  else if (typed === "?") showHelp();
  else if (typed === "/") {
    if (!focusSearch()) openPalette();
  } else if (typed === "[") toggleSidebar();
  else if (typed === "g d") activate(document.querySelector(".brand-link"));
  else if (typed === "g") {
    pending = "g";
    window.clearTimeout(timer);
    timer = window.setTimeout(reset, SEQUENCE_MS);
    event.preventDefault();
    return;
  } else handled = false;
  reset();
  if (handled) event.preventDefault();
}

export function initShortcuts() {
  document.addEventListener("keydown", onKeydown);
}
