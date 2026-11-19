// Tabs ([data-console-tabs]) with URL-hash sync.
//   buttons [role=tab][data-console-tab][aria-controls=<panel id>]
//   panels  [role=tabpanel][data-console-tabpanel]   (visible without JS; inactive ones get `hidden`)
// Initial tab: the one named by location.hash (panel or tab id), else the first tab whose panel
// holds `[aria-invalid=true], .is-invalid, .errorlist`, else the aria-selected / first tab.
// Fields inside hidden panels that fail native validation reveal their tab first (the `invalid`
// event is captured here), so a submit never fails silently. Dispatch `console-reveal` from
// any element to bring its tab forward.
// Arrow/Home/End keys move between tabs (RTL aware); selecting a tab updates the hash.
import { EVENTS, emit } from "../lib/util.js";

const ERROR_SELECTOR = '[aria-invalid="true"], .is-invalid, .errorlist';

export function attachTabs(root) {
  const controller = new AbortController();
  const { signal } = controller;
  const tabs = [...root.querySelectorAll("[role=tab][data-console-tab]")].filter(
    (tab) => tab.closest("[data-console-tabs]") === root,
  );
  const panelOf = (tab) => document.getElementById(tab.getAttribute("aria-controls") ?? "");
  const tabOf = (panel) => tabs.find((tab) => panelOf(tab) === panel);

  function select(tab, { focus = false, updateHash = true } = {}) {
    if (!tab) return;
    for (const other of tabs) {
      const active = other === tab;
      other.setAttribute("aria-selected", String(active));
      other.tabIndex = active ? 0 : -1;
      const panel = panelOf(other);
      if (panel) panel.hidden = !active;
    }
    if (focus) tab.focus();
    const panel = panelOf(tab);
    if (updateHash && panel?.id) {
      window.history.replaceState(window.history.state, "", `#${panel.id}`);
    }
    emit(EVENTS.tabChange, { tab, panel }, root);
  }

  const revealFor = (element) => {
    const panel = element instanceof Element ? element.closest("[data-console-tabpanel]") : null;
    const tab = panel ? tabOf(panel) : null;
    if (tab && tab.getAttribute("aria-selected") !== "true") select(tab, { updateHash: false });
  };

  root.addEventListener("keydown", (event) => {
    const current = event.target instanceof Element ? event.target.closest("[data-console-tab]") : null;
    if (!current || !tabs.includes(current)) return;
    const rtl = getComputedStyle(root).direction === "rtl";
    const step = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[event.key];
    let next = null;
    if (step) next = tabs[(tabs.indexOf(current) + step + tabs.length) % tabs.length];
    else if (event.key === "Home") next = tabs[0];
    else if (event.key === "End") next = tabs[tabs.length - 1];
    if (next) {
      event.preventDefault();
      select(next, { focus: true });
    }
  }, { signal });

  root.addEventListener("click", (event) => {
    const tab = event.target instanceof Element ? event.target.closest("[data-console-tab]") : null;
    if (tab && tabs.includes(tab)) {
      event.preventDefault();
      select(tab);
    }
  }, { signal });

  // `invalid` does not bubble; capture it so hidden required fields reveal their tab.
  root.addEventListener("invalid", (event) => revealFor(event.target), { capture: true, signal });
  root.addEventListener(EVENTS.reveal, (event) => revealFor(event.target), { signal });
  window.addEventListener("hashchange", () => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    const target = tabs.find((tab) => tab.id === id || tab.getAttribute("aria-controls") === id);
    if (target) select(target, { updateHash: false });
  }, { signal });

  const id = decodeURIComponent(window.location.hash.slice(1));
  const fromHash = tabs.find((tab) => tab.id === id || tab.getAttribute("aria-controls") === id);
  const withError = tabs.find((tab) => panelOf(tab)?.querySelector(ERROR_SELECTOR));
  const preselected = tabs.find((tab) => tab.getAttribute("aria-selected") === "true");
  select(fromHash ?? withError ?? preselected ?? tabs[0], { updateHash: false });

  return { destroy: () => controller.abort() };
}
