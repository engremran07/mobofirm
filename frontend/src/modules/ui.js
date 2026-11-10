// Core UI behaviours of the design system (docs/05-frontend/design-system.md). Everything
// here is progressive enhancement: pages stay usable without it, and no behaviour relies
// on inline scripts or styles (strict CSP).
import { showToast } from "./toasts.js";

const MODAL_OPEN = "dialog[open]:modal";

function anotherModalIsOpen(except) {
  return [...document.querySelectorAll(MODAL_OPEN)].some((dialog) => dialog !== except);
}

// ---- Command palette -------------------------------------------------------------
let paletteOpener = null;

function palette() {
  const dialog = document.getElementById("command-palette");
  return dialog instanceof HTMLDialogElement ? dialog : null;
}

export function openPalette(opener = document.activeElement) {
  const dialog = palette();
  if (!dialog || dialog.open || anotherModalIsOpen(dialog)) return;
  paletteOpener = opener instanceof HTMLElement ? opener : null;
  dialog.showModal();
  document.getElementById("palette-search")?.focus();
}

export function closePalette() {
  const dialog = palette();
  if (dialog?.open) dialog.close();
}

function initializePalette() {
  const dialog = palette();
  // Capture phase: Ctrl/Cmd+K belongs to the palette even if another module also
  // listens for it (modules/search.js focuses the header search on "/" and as a fallback
  // when no palette is rendered).
  document.addEventListener(
    "keydown",
    (event) => {
      const combo = (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey;
      if (!dialog || !combo || event.key.toLowerCase() !== "k") return;
      // The rich-text editor uses Ctrl+K for links; leave it alone there.
      if (event.target instanceof Element && event.target.closest(".note-editor, [contenteditable='true']")) {
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      if (dialog.open) closePalette();
      else openPalette();
    },
    true,
  );
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const trigger = event.target.closest("[data-command-palette-open]");
    if (trigger) {
      openPalette(trigger);
      return;
    }
    if (dialog?.open && event.target.closest("[data-cmdk-close]")) closePalette();
  });
  if (!dialog) return;
  // Backdrop: a press that starts and ends on the dialog element itself closes it.
  let pressedOnBackdrop = false;
  dialog.addEventListener("mousedown", (event) => {
    pressedOnBackdrop = event.target === dialog;
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && pressedOnBackdrop) closePalette();
    pressedOnBackdrop = false;
  });
  dialog.addEventListener("close", () => {
    if (paletteOpener?.isConnected) paletteOpener.focus({ preventScroll: true });
    paletteOpener = null;
  });
}

// ---- Mega menu -------------------------------------------------------------------
function closeMega(except = null) {
  for (const item of document.querySelectorAll("[data-nav-item][data-open='true']")) {
    if (item === except) continue;
    item.dataset.open = "false";
    item.querySelector("[data-mega-toggle]")?.setAttribute("aria-expanded", "false");
  }
}

function initializeMega() {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const toggle = event.target.closest("[data-mega-toggle]");
    if (!toggle) {
      if (!event.target.closest("[data-nav-item]")) closeMega();
      return;
    }
    const item = toggle.closest("[data-nav-item]");
    const open = item.dataset.open !== "true";
    closeMega(item);
    item.dataset.open = String(open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open) item.querySelector(".mega-link")?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = document.querySelector("[data-nav-item][data-open='true']");
    if (!open) return;
    closeMega();
    open.querySelector("[data-mega-toggle]")?.focus();
  });
  document.addEventListener("focusout", (event) => {
    const item = event.target instanceof Element ? event.target.closest("[data-nav-item]") : null;
    if (!item || item.dataset.open !== "true") return;
    if (!(event.relatedTarget instanceof Node) || !item.contains(event.relatedTarget)) closeMega();
  });
}

// ---- Tabs ------------------------------------------------------------------------
function selectTab(tab) {
  const list = tab.closest("[role='tablist']");
  if (!list) return;
  for (const other of list.querySelectorAll("[role='tab']")) {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(other.getAttribute("aria-controls") ?? "");
    if (panel) panel.hidden = !selected;
  }
}

function initializeTabs() {
  document.addEventListener("click", (event) => {
    const tab = event.target instanceof Element ? event.target.closest("[role='tab']") : null;
    if (tab && tab.closest("[data-tabs]")) selectTab(tab);
  });
  document.addEventListener("keydown", (event) => {
    const tab = event.target instanceof Element ? event.target.closest("[role='tab']") : null;
    const list = tab?.closest("[data-tabs]");
    if (!tab || !list) return;
    const tabs = [...list.querySelectorAll("[role='tab']")];
    const index = tabs.indexOf(tab);
    const next = {
      ArrowRight: tabs[(index + 1) % tabs.length],
      ArrowLeft: tabs[(index - 1 + tabs.length) % tabs.length],
      Home: tabs[0],
      End: tabs[tabs.length - 1],
    }[event.key];
    if (!next) return;
    event.preventDefault();
    next.focus();
    selectTab(next);
  });
}

// ---- Scroll reveal + sticky header state ----------------------------------------
function initializeReveal() {
  const reveal = (element) => element.classList.add("is-revealed");
  if (typeof IntersectionObserver !== "function") {
    document.querySelectorAll("[data-reveal]").forEach(reveal);
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        reveal(entry.target);
        observer.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.08 },
  );
  const scan = (root) => {
    [...root.querySelectorAll("[data-reveal]:not(.is-revealed)")].forEach((element, index) => {
      element.style.setProperty("--reveal-index", String(Math.min(index, 8)));
      observer.observe(element);
    });
  };
  scan(document);
  document.addEventListener("htmx:afterSwap", (event) => {
    scan(event.target instanceof Element ? event.target : document);
  });
}

function initializeHeaderState() {
  const header = document.querySelector(".site-header");
  if (!header) return;
  let queued = false;
  const update = () => {
    queued = false;
    header.dataset.scrolled = String(window.scrollY > 8);
  };
  window.addEventListener(
    "scroll",
    () => {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(update);
    },
    { passive: true },
  );
  update();
}

// ---- Copy to clipboard -----------------------------------------------------------
async function writeClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      // Fall through to the legacy path (permission denied or insecure context).
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.className = "u-visually-hidden";
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (error) {
    copied = false;
  }
  area.remove();
  return copied;
}

function copySource(button) {
  if (button.hasAttribute("data-copy-url")) return window.location.href;
  const targetSelector = button.dataset.copyTarget;
  if (targetSelector) {
    const target = document.querySelector(targetSelector);
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      return target.value;
    }
    return target?.textContent?.trim() ?? "";
  }
  return button.dataset.copy ?? "";
}

function initializeCopy() {
  document.addEventListener("click", async (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-copy], [data-copy-target], [data-copy-url]") : null;
    if (!button) return;
    const text = copySource(button);
    if (!text) return;
    const copied = await writeClipboard(text);
    if (!copied) {
      showToast({ level: "danger", message: "Copying is not available in this browser." });
      return;
    }
    button.dataset.copied = "true";
    window.setTimeout(() => delete button.dataset.copied, 1800);
    showToast({ level: "success", message: button.dataset.copyMessage ?? "Copied to the clipboard.", duration: 2500 });
  });
}

// ---- View transitions (same-document, with a plain fallback) ---------------------
// Opt in per swap with hx-swap="innerHTML transition:true", or call this helper.
export function withViewTransition(update) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typeof document.startViewTransition === "function" && !reduce) {
    return document.startViewTransition(update).finished;
  }
  return Promise.resolve(update());
}

// ---- Optimistic UI ---------------------------------------------------------------
// <button hx-post="..." data-optimistic data-optimistic-toggle="aria-pressed"> flips the
// attribute immediately and reverts it when the request fails.
const optimisticState = new WeakMap();

function initializeOptimistic() {
  document.addEventListener("htmx:beforeRequest", (event) => {
    const source = event.detail.elt instanceof Element ? event.detail.elt.closest("[data-optimistic]") : null;
    if (!source) return;
    const attribute = source.dataset.optimisticToggle;
    const previous = attribute ? source.getAttribute(attribute) : null;
    optimisticState.set(source, { attribute, previous });
    if (attribute) source.setAttribute(attribute, previous === "true" ? "false" : "true");
    source.classList.add("is-pending");
    source.setAttribute("aria-busy", "true");
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    const source = event.detail.elt instanceof Element ? event.detail.elt.closest("[data-optimistic]") : null;
    const state = source ? optimisticState.get(source) : null;
    if (!source || !state) return;
    source.classList.remove("is-pending");
    source.removeAttribute("aria-busy");
    if (!event.detail.successful && state.attribute) {
      if (state.previous === null) source.removeAttribute(state.attribute);
      else source.setAttribute(state.attribute, state.previous);
    }
    optimisticState.delete(source);
  });
}

// ---- Loading state ---------------------------------------------------------------
// HTMX only adds .htmx-request to indicators, and the page-level indicator is the top
// bar. So the element issuing a request (and a .skeleton-host target) is marked with
// data-loading for the duration of the request: buttons show a spinner, forms disable
// double submits visually, and skeleton hosts dim with a shimmer. CSS does the rest.
const loadingCounts = new WeakMap();

function loadingTargets(detail) {
  const targets = new Set();
  const elt = detail.elt;
  if (elt instanceof Element) targets.add(elt);
  const target = detail.target;
  if (target instanceof Element && target.matches(".skeleton-host")) targets.add(target);
  return targets;
}

function initializeLoading() {
  document.addEventListener("htmx:beforeRequest", (event) => {
    for (const element of loadingTargets(event.detail)) {
      loadingCounts.set(element, (loadingCounts.get(element) ?? 0) + 1);
      element.setAttribute("data-loading", "");
    }
  });
  document.addEventListener("htmx:afterRequest", (event) => {
    for (const element of loadingTargets(event.detail)) {
      const count = (loadingCounts.get(element) ?? 1) - 1;
      loadingCounts.set(element, count);
      if (count <= 0) element.removeAttribute("data-loading");
    }
  });
}

export function initializeUi() {
  initializePalette();
  initializeMega();
  initializeTabs();
  initializeReveal();
  initializeHeaderState();
  initializeCopy();
  initializeLoading();
  initializeOptimistic();
}
