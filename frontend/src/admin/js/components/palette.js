// Command palette / global search: the native <dialog> #console-palette (shell header trigger
// carries data-ui-dialog-open="console-palette"; the trigger is a real link to
// the search page, so it also works without JavaScript).
//
// Ctrl/Cmd+K toggles it. Typing is handled by htmx (the input carries hx-get into
// #console-palette-results). Keyboard: ArrowDown moves from the box into the results (real
// focus on the links, so screen readers follow it), ArrowUp/ArrowDown walk them, Home/End jump,
// typing sends focus back to the box, Enter in the box opens the first result.
import { NativeModal as Modal } from "../ui/modal.js";
import { EVENTS, emit, isVisible } from "../lib/util.js";

const ID = "#console-palette";
const ITEM = ".c-palette__item";

const palette = () => document.querySelector(ID);
const input = () => palette()?.querySelector(".c-palette__input");
const results = () => document.getElementById("console-palette-results");
const items = () => [...(results()?.querySelectorAll(ITEM) ?? [])].filter(isVisible);

let initialNodes = null;
const snapshot = () => {
  const list = results();
  if (!initialNodes && list) initialNodes = [...list.childNodes].map((node) => node.cloneNode(true));
};

export function openPalette() {
  const root = palette();
  if (!root || document.querySelector(".modal.show:not(#console-palette)")) return;
  emit(EVENTS.paletteOpen, {});
  Modal.getOrCreateInstance(root).show();
}

function setActive(item) {
  for (const other of results()?.querySelectorAll(`${ITEM}.is-active`) ?? []) other.classList.remove("is-active");
  item?.classList.add("is-active");
}

function onKeydown(event) {
  const root = palette();
  if (!root?.open || !(event.target instanceof Element) || !root.contains(event.target)) return;
  const list = items();
  const index = list.indexOf(event.target.closest(ITEM));
  const inInput = event.target === input();
  if (event.key === "ArrowDown") {
    event.preventDefault();
    list[Math.min(index + 1, list.length - 1)]?.focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    if (index <= 0) input()?.focus();
    else list[index - 1].focus();
  } else if (event.key === "Home" && !inInput && list.length) {
    event.preventDefault();
    list[0].focus();
  } else if (event.key === "End" && !inInput && list.length) {
    event.preventDefault();
    list.at(-1).focus();
  } else if (event.key === "Enter" && inInput && list.length && !document.documentElement.hasAttribute("data-console-busy")) {
    const typed = input()?.value.trim() ?? "";
    if (typed.length >= 2 || typed === "") {
      event.preventDefault();
      list[0].click();
    }
  } else if (!inInput && index >= 0 && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    input()?.focus();
  }
}

export function initPalette() {
  document.addEventListener("click", (event) => {
    const trigger = event.target instanceof Element ? event.target.closest('[data-ui-dialog-open="console-palette"]') : null;
    if (!trigger) return;
    event.preventDefault();
    openPalette();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      const root = palette();
      if (root?.open) Modal.getInstance(root)?.hide();
      else openPalette();
    }
  });
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("focusin", (event) => {
    const item = event.target instanceof Element ? event.target.closest(ITEM) : null;
    if (item && palette()?.contains(item)) setActive(item);
  });
  document.addEventListener("shown.ui.modal", (event) => {
    if (event.target !== palette()) return;
    snapshot();
    input()?.focus();
    input()?.select();
  });
  document.addEventListener("hidden.ui.modal", (event) => {
    const box = input();
    if (event.target !== palette() || !box) return;
    box.value = "";
    const list = results();
    if (list && initialNodes) list.replaceChildren(...initialNodes.map((node) => node.cloneNode(true)));
  });
  document.addEventListener("show.ui.modal", (event) => {
    if (event.target === palette()) snapshot();
  });
}
