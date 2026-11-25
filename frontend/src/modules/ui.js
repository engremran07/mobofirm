// Core UI behaviours of the design system (docs/05-frontend/design-system.md). Everything
// here is progressive enhancement: pages stay usable without it, and no behaviour relies
// on inline scripts or styles (strict CSP).
import { showToast } from "./toasts.js";

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

// ---- Public account menu ---------------------------------------------------------
function initializeAccountMenu() {
  const closeOutside = (target) => {
    const activeMenu =
      target instanceof Element ? target.closest(".site-account-menu") : null;
    for (const menu of document.querySelectorAll(".site-account-menu[open]")) {
      if (menu !== activeMenu) menu.removeAttribute("open");
    }
  };

  document.addEventListener("click", (event) => closeOutside(event.target));
  document.addEventListener("focusin", (event) => closeOutside(event.target));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const menu = document.querySelector(".site-account-menu[open]");
    if (!menu) return;
    menu.removeAttribute("open");
    menu.querySelector("summary")?.focus();
  });
}

// Keep link groups open on wide screens and compact on narrow and medium screens.
// The open attributes in the template preserve the full navigation without JavaScript.
function initializeFooterGroups() {
  const groups = document.querySelectorAll("[data-footer-group]");
  if (!groups.length) return;
  const compact = window.matchMedia("(max-width: 1199.98px)");
  const sync = () => {
    for (const group of groups) group.open = !compact.matches;
  };
  sync();
  compact.addEventListener("change", sync);
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
  document.addEventListener("htmx:after:swap", (event) => {
    const target = event.detail.ctx.target;
    scan(target instanceof Element ? target : document);
  });
}

function initializeHeaderState() {
  const header = document.querySelector(".site-header");
  if (!header) return;
  const movementThreshold = 12;
  const hideAfter = 96;
  let anchorY = Math.max(0, window.scrollY);
  let queued = false;
  const update = () => {
    queued = false;
    const scrollY = Math.max(0, window.scrollY);
    header.dataset.scrolled = String(scrollY > 8);

    const navigationOpen = header.querySelector(
      "[data-nav-item][data-open='true'], .mobile-nav-toggle[aria-expanded='true']",
    );
    if (scrollY <= 8 || header.contains(document.activeElement) || navigationOpen) {
      header.removeAttribute("data-header-hidden");
      anchorY = scrollY;
      return;
    }

    if (scrollY - anchorY > movementThreshold) {
      if (scrollY > hideAfter) header.setAttribute("data-header-hidden", "true");
      anchorY = scrollY;
    } else if (anchorY - scrollY > movementThreshold) {
      header.removeAttribute("data-header-hidden");
      anchorY = scrollY;
    }
  };
  header.addEventListener("focusin", () => {
    header.removeAttribute("data-header-hidden");
    anchorY = Math.max(0, window.scrollY);
  });
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

// ---- Download text as a file -----------------------------------------------------
// <button data-download-target="#codes" data-download-filename="codes.txt"> saves the
// target's text locally; nothing is sent to the server.
function initializeDownload() {
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-download-target]") : null;
    if (!button) return;
    const target = document.querySelector(button.dataset.downloadTarget);
    const text = target instanceof HTMLTextAreaElement ? target.value : (target?.textContent?.trim() ?? "");
    if (!text) return;
    const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = button.dataset.downloadFilename ?? "download.txt";
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast({ level: "success", message: button.dataset.downloadMessage ?? "File saved.", duration: 2500 });
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
  document.addEventListener("htmx:before:request", (event) => {
    const elt = event.detail.ctx.sourceElement;
    const source = elt instanceof Element ? elt.closest("[data-optimistic]") : null;
    if (!source) return;
    const attribute = source.dataset.optimisticToggle;
    const previous = attribute ? source.getAttribute(attribute) : null;
    optimisticState.set(source, { attribute, previous });
    if (attribute) source.setAttribute(attribute, previous === "true" ? "false" : "true");
    source.classList.add("is-pending");
    source.setAttribute("aria-busy", "true");
  });
  document.addEventListener("htmx:after:request", (event) => {
    const ctx = event.detail.ctx;
    const elt = ctx.sourceElement;
    const source = elt instanceof Element ? elt.closest("[data-optimistic]") : null;
    const state = source ? optimisticState.get(source) : null;
    if (!source || !state) return;
    source.classList.remove("is-pending");
    source.removeAttribute("aria-busy");
    if (!ctx.response?.raw?.ok && state.attribute) {
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
  const elt = detail.sourceElement;
  if (elt instanceof Element) targets.add(elt);
  const target = detail.target;
  if (target instanceof Element && target.matches(".skeleton-host")) targets.add(target);
  return targets;
}

function initializeLoading() {
  document.addEventListener("htmx:before:request", (event) => {
    for (const element of loadingTargets(event.detail.ctx)) {
      loadingCounts.set(element, (loadingCounts.get(element) ?? 0) + 1);
      element.setAttribute("data-loading", "");
    }
  });
  document.addEventListener("htmx:after:request", (event) => {
    for (const element of loadingTargets(event.detail.ctx)) {
      const count = (loadingCounts.get(element) ?? 1) - 1;
      loadingCounts.set(element, count);
      if (count <= 0) element.removeAttribute("data-loading");
    }
  });
}

export function initializeUi() {
  initializeMega();
  initializeAccountMenu();
  initializeFooterGroups();
  initializeTabs();
  initializeReveal();
  initializeHeaderState();
  initializeCopy();
  initializeDownload();
  initializeLoading();
  initializeOptimistic();
}
