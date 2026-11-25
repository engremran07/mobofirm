// Sidebar: retractable navigation.
//   * every group is a disclosure (`button.c-nav-toggle[aria-expanded][aria-controls]` + `.c-nav-panel`),
//     animated by CSS (grid rows), keyboard operable, persisted per user in localStorage, and the group
//     holding the current page opens by default (also after an htmx navigation);
//   * collapse all and a live filter; at most one navigation group stays open;
//   * desktop: the whole sidebar collapses to an icon rail (persisted) with tooltips; mobile: off-canvas drawer.
//
// Hooks: aside[data-console-sidebar][data-console-user], [data-console-sidebar-toggle] (button, anywhere in
// the header), [data-console-sidebar-overlay] (click-away layer), li[data-console-nav-group="slug"],
// [data-console-nav-all="collapse"], input[data-console-nav-filter],
// [data-console-nav-empty]. NB: <html> carries the *state* attribute `data-console-sidebar`, so every
// lookup of the sidebar itself is scoped with `aside[...]`.
// State is published as attributes so CSS needs no JS knowledge:
//   <html data-console-sidebar="expanded|mini|open|closed">   (open/closed = mobile drawer)
//   <body> gets AdminLTE's `sidebar-collapse sidebar-without-hover` (desktop mini) / `sidebar-open` (mobile drawer)
// The toggle's aria-expanded mirrors visibility. Breakpoint: 992px (Bootstrap `lg`).
import { EVENTS, debounce, emit, focusables, storage } from "../lib/util.js";

const COLLAPSED_KEY = "mobofirm-console-sidebar";
const GROUPS_KEY = "mobofirm-console-nav-groups";
const mobileQuery = window.matchMedia("(max-width: 991.98px)");

let collapsed = storage.get(COLLAPSED_KEY) === "mini";
let drawerOpen = false;
let lastFocus = null;

const sidebar = () => document.querySelector("aside[data-console-sidebar]");
const groups = () => [...(sidebar()?.querySelectorAll("[data-console-nav-group]") ?? [])];
// Navigation state is per user: another account on the same browser starts from the defaults.
const userKey = (base) => `${base}:${sidebar()?.dataset.consoleUser || "anonymous"}`;

export function sidebarState() {
  const mobile = mobileQuery.matches;
  return { mobile, collapsed: !mobile && collapsed, open: mobile && drawerOpen };
}

function apply() {
  const { mobile, collapsed: mini, open } = sidebarState();
  const root = document.documentElement;
  root.dataset.consoleSidebar = mobile ? (open ? "open" : "closed") : mini ? "mini" : "expanded";
  document.body.classList.toggle("sidebar-collapse", mini);
  document.body.classList.toggle("sidebar-without-hover", mini);
  document.body.classList.toggle("sidebar-open", open);
  for (const toggle of document.querySelectorAll("[data-console-sidebar-toggle]")) {
    toggle.setAttribute("aria-expanded", String(mobile ? open : !mini));
  }
  for (const overlay of document.querySelectorAll("[data-console-sidebar-overlay]")) {
    overlay.toggleAttribute("data-open", open);
  }
  for (const region of document.querySelectorAll("main.app-main, footer.app-footer")) {
    region.toggleAttribute("inert", open);
  }
  emit(EVENTS.sidebarChange, sidebarState());
}

export function closeDrawer({ restoreFocus = true } = {}) {
  if (!drawerOpen) return;
  drawerOpen = false;
  apply();
  if (restoreFocus && lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
}

export function toggleSidebar(opener = null) {
  if (mobileQuery.matches) {
    drawerOpen = !drawerOpen;
    if (drawerOpen) {
      lastFocus = opener ?? document.activeElement;
      apply();
      sidebar()?.querySelector('a[aria-current="page"], .c-nav-toggle, a[href]')?.focus({ preventScroll: true });
      return;
    }
    closeDrawer();
    return;
  }
  collapsed = !collapsed;
  storage.set(COLLAPSED_KEY, collapsed ? "mini" : "full");
  apply();
}

// -- groups ------------------------------------------------------------------------------
function storedGroups() {
  try {
    const value = JSON.parse(storage.get(userKey(GROUPS_KEY)) ?? "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function saveGroups() {
  const state = {};
  for (const group of groups()) state[group.dataset.consoleNavGroup] = isOpen(group);
  storage.set(userKey(GROUPS_KEY), JSON.stringify(state));
}

const panelOf = (group) => group.querySelector(":scope > .c-nav-panel");
const toggleOf = (group) => group.querySelector(":scope > .c-nav-toggle");
const isOpen = (group) => panelOf(group)?.hasAttribute("data-open") ?? false;
function syncAria(group) {
  const shown = isOpen(group) || group.hasAttribute("data-filter-open");
  toggleOf(group)?.setAttribute("aria-expanded", String(shown));
  group.classList.toggle("menu-open", shown);
}

function setGroup(group, open) {
  panelOf(group)?.toggleAttribute("data-open", open);
  syncAria(group);
}

function collapseAll() {
  for (const group of groups()) setGroup(group, false);
  saveGroups();
  announceNav("All navigation groups collapsed");
}

function announceNav(message) {
  const region = document.getElementById("console-announcer");
  if (region) region.textContent = message;
}

function openGroup(group, { persist = true } = {}) {
  for (const other of groups()) if (other !== group) setGroup(other, false);
  setGroup(group, true);
  if (persist) saveGroups();
}

// Stored choices apply to every group; the group holding the current page is always opened.
function restoreGroups() {
  const stored = storedGroups();
  for (const group of groups()) {
    const remembered = stored[group.dataset.consoleNavGroup];
    if (typeof remembered === "boolean") setGroup(group, remembered);
    else syncAria(group);
  }
  const current = sidebar()?.querySelector('a[aria-current="page"]')?.closest("[data-console-nav-group]");
  if (current) {
    setGroup(current, true);
    saveGroups();
  }
  const keep = current ?? groups().find(isOpen);
  for (const group of groups()) if (group !== keep) setGroup(group, false);
  if (keep && !isOpen(keep)) setGroup(keep, true);
  saveGroups();
}

function onGroupToggle(button) {
  const group = button.closest("[data-console-nav-group]");
  if (!group) return;
  if (sidebarState().collapsed) {
    // In the icon rail a group icon expands the sidebar and shows that group.
    collapsed = false;
    storage.set(COLLAPSED_KEY, "full");
    openGroup(group);
    apply();
    button.focus({ preventScroll: true });
    return;
  }
  if (isOpen(group)) {
    setGroup(group, false);
    saveGroups();
  } else {
    openGroup(group);
  }
}

// -- active page ---------------------------------------------------------------------------
// Same rule as apps.core.admin_navigation: the longest path that equals or prefixes the current one.
export function syncActiveNavigation() {
  const root = sidebar();
  if (!root) return;
  const here = window.location.pathname;
  let best = null;
  let bestLength = -1;
  const indexPath = new URL(root.querySelector(".brand-link")?.href ?? "/", window.location.href).pathname;
  const links = [...root.querySelectorAll("a.nav-link[href]")];
  for (const link of links) {
    const path = new URL(link.href, window.location.href).pathname;
    const matches = path === indexPath ? here === path : here === path || here.startsWith(path.replace(/\/$/, "") + "/");
    if (matches && path.length > bestLength) {
      best = link;
      bestLength = path.length;
    }
  }
  for (const link of links) {
    const active = link === best;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const group of groups()) {
    const holds = group.contains(best);
    toggleOf(group)?.classList.toggle("active", holds);
  }
  const current = best?.closest("[data-console-nav-group]");
  if (current && !isOpen(current)) {
    openGroup(current);
  }
}

// -- filter ----------------------------------------------------------------------------------
const labelOf = (element) => (element.querySelector(":scope > .nav-link p, :scope > p") ?? element).textContent.trim().toLowerCase();

function applyFilter(query) {
  const root = sidebar();
  const menu = root?.querySelector(".sidebar-menu");
  if (!menu) return;
  const needle = query.trim().toLowerCase();
  root.toggleAttribute("data-filtering", needle !== "");
  let header = null;
  let headerHits = 0;
  let total = 0;
  const closeHeader = () => {
    if (header) header.hidden = needle !== "" && headerHits === 0;
  };
  for (const item of menu.children) {
    if (item.classList.contains("nav-header")) {
      closeHeader();
      header = item;
      headerHits = 0;
    } else if (item.matches("[data-console-nav-group]")) {
      closeHeader();
      header = null;
      const groupHit = needle !== "" && labelOf(item).includes(needle);
      let shown = 0;
      for (const entry of item.querySelectorAll(".nav-treeview > li")) {
        const hit = needle === "" || groupHit || labelOf(entry).includes(needle);
        entry.hidden = !hit;
        if (hit) shown += 1;
      }
      item.hidden = needle !== "" && shown === 0;
      item.toggleAttribute("data-filter-open", needle !== "" && shown > 0);
      syncAria(item);
      total += shown;
    } else {
      const hit = needle === "" || labelOf(item).includes(needle);
      item.hidden = !hit;
      if (hit) {
        headerHits += 1;
        total += 1;
      }
    }
  }
  closeHeader();
  for (const empty of root.querySelectorAll("[data-console-nav-empty]")) empty.hidden = needle === "" || total > 0;
}

// -- keyboard: arrows walk the visible navigation entries -------------------------------------
function onNavKeydown(event) {
  const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
  if (!keys.includes(event.key) || !(event.target instanceof Element)) return;
  const root = sidebar();
  if (!root?.contains(event.target)) return;
  const input = root.querySelector("[data-console-nav-filter]");
  if (event.target === input) {
    if (event.key !== "ArrowDown") return;
    applyFilter(input.value); // the debounced filter may still be pending: walk the narrowed list
  }
  const entries = focusables(root.querySelector(".sidebar-wrapper") ?? root);
  if (event.target === input) {
    if (!entries.length) return;
    event.preventDefault();
    entries[0].focus();
    return;
  }
  const index = entries.indexOf(event.target.closest(".nav-link"));
  if (index < 0) return;
  event.preventDefault();
  let next = index;
  if (event.key === "ArrowDown") next = Math.min(entries.length - 1, index + 1);
  else if (event.key === "ArrowUp") next = index - 1;
  else if (event.key === "Home") next = 0;
  else next = entries.length - 1;
  if (next < 0) input?.focus();
  else entries[next]?.focus();
}

// -- rail tooltips -----------------------------------------------------------------------------
let tip = null;

function hideTip() {
  tip?.remove();
  tip = null;
}

function showTip(target) {
  if (!sidebarState().collapsed || !target.dataset.navLabel) return;
  hideTip();
  tip = document.createElement("div");
  tip.className = "c-nav-tip";
  tip.setAttribute("role", "tooltip");
  tip.textContent = target.dataset.navLabel;
  document.body.append(tip);
  const box = target.getBoundingClientRect();
  const rtl = document.documentElement.dir === "rtl";
  tip.style.insetBlockStart = `${box.top + box.height / 2}px`;
  if (rtl) tip.style.right = `${document.documentElement.clientWidth - box.left + 8}px`;
  else tip.style.left = `${box.right + 8}px`;
}

function onTipTarget(event) {
  const target = event.target instanceof Element ? event.target.closest("aside[data-console-sidebar] [data-nav-label]") : null;
  if (target) showTip(target);
}

export function syncSidebar() {
  restoreGroups();
  syncActiveNavigation();
  apply();
}

export function initSidebar() {
  apply();
  restoreGroups();
  const onFilter = debounce((input) => applyFilter(input.value), 80);
  document.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.matches("[data-console-nav-filter]")) {
      onFilter(event.target);
    }
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const toggle = event.target.closest("[data-console-sidebar-toggle]");
    if (toggle) {
      toggleSidebar(toggle);
      return;
    }
    const groupToggle = event.target.closest("aside[data-console-sidebar] .c-nav-toggle");
    if (groupToggle) {
      onGroupToggle(groupToggle);
      return;
    }
    const collapse = event.target.closest("[data-console-nav-all='collapse']");
    if (collapse) {
      collapseAll();
      return;
    }
    if (event.target.closest("[data-console-sidebar-overlay]")) {
      closeDrawer();
      return;
    }
    // Following a link inside the drawer closes it.
    if (drawerOpen && event.target.closest("aside[data-console-sidebar] a[href]")) closeDrawer({ restoreFocus: false });
  });
  document.addEventListener("keydown", (event) => {
    const filter = event.target instanceof Element && event.target.matches("[data-console-nav-filter]");
    if (event.key === "Escape" && filter && event.target.value) {
      event.preventDefault();
      event.target.value = "";
      applyFilter("");
      return;
    }
    if (event.key === "Escape") hideTip();
    if (event.key === "Escape" && drawerOpen && !document.querySelector(".modal.show")) {
      event.preventDefault();
      closeDrawer();
      return;
    }
    onNavKeydown(event);
  });
  document.addEventListener("mouseover", onTipTarget);
  document.addEventListener("focusin", onTipTarget);
  document.addEventListener("mouseout", hideTip);
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", hideTip, true);
  mobileQuery.addEventListener("change", () => {
    drawerOpen = false;
    hideTip();
    apply();
  });
  document.addEventListener(EVENTS.navigated, () => closeDrawer({ restoreFocus: false }));
  document.addEventListener("htmx:after:swap", (event) => {
    const ctx = event.detail?.ctx;
    const target = ctx?.target;
    const restored = Boolean(ctx?.request?.headers?.["HX-History-Restore-Request"]);
    if (restored || target === document.body || target?.id === "console-main" || target?.contains?.(sidebar())) {
      hideTip();
      syncSidebar();
    }
  });
}

// x-data="consoleSidebar": reactive mirror (`collapsed`, `open`, `mobile`) plus `toggle()`.
export function sidebarComponent() {
  return {
    collapsed: false,
    open: false,
    mobile: false,
    init() {
      this.sync = () => Object.assign(this, sidebarState());
      this.sync();
      document.addEventListener(EVENTS.sidebarChange, this.sync);
    },
    destroy() {
      document.removeEventListener(EVENTS.sidebarChange, this.sync);
    },
    toggle(event) {
      toggleSidebar(event?.currentTarget ?? null);
    },
    close() {
      closeDrawer();
    },
  };
}
