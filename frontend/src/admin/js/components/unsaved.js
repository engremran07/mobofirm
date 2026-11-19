// Unsaved-changes guard for `form[data-console-unsaved-guard]`.
//
// A form is dirty when its serialized values differ from the snapshot taken when it
// appeared (CSRF token and file inputs excluded; files compare by name and size). Leaving
// asks through the app-owned confirm dialog, never window.confirm:
//   * plain links           -> intercepted here, then followed
//   * htmx/boosted requests -> `htmx:config:request` marks them with a confirm question that
//                              `htmx:confirm` (htmx-setup.js) answers with this dialog
//   * dialogs               -> dialog.js asks before hiding a dialog that holds a dirty form
//   * tab close/refresh     -> the one browser-mandated `beforeunload` fallback (no app UI
//                              is possible there), armed only while a guarded form is dirty
// Optional copy: data-console-unsaved-title|-message|-confirm|-cancel on the form.
import { confirmDialog } from "./confirm.js";

export const UNSAVED_QUESTION = "console-unsaved";
const SELECTOR = "form[data-console-unsaved-guard], form[data-unsaved-guard]";
const baselines = new WeakMap();
const submitted = new WeakSet();

function serialize(form) {
  const parts = [];
  for (const [key, value] of new FormData(form)) {
    if (key === "csrfmiddlewaretoken") continue;
    parts.push(`${key}=${value instanceof File ? `${value.name}:${value.size}` : value}`);
  }
  return parts.join("&");
}

function snapshot(root = document) {
  const forms = root instanceof HTMLFormElement && root.matches(SELECTOR) ? [root] : [];
  forms.push(...root.querySelectorAll(SELECTOR));
  for (const form of forms) {
    if (!baselines.has(form)) baselines.set(form, serialize(form));
  }
}

function isDirty(form) {
  return (
    form.isConnected && baselines.has(form) && !submitted.has(form) && serialize(form) !== baselines.get(form)
  );
}

export function dirtyForms(root = document) {
  const forms = root instanceof HTMLFormElement && root.matches(SELECTOR) ? [root] : [];
  forms.push(...root.querySelectorAll(SELECTOR));
  return forms.filter(isDirty);
}

export function discardChanges(forms) {
  for (const form of forms) baselines.set(form, serialize(form));
}

export function askToDiscard(forms) {
  const data = forms[0]?.dataset ?? {};
  return confirmDialog({
    title: data.consoleUnsavedTitle ?? "Discard unsaved changes?",
    message:
      data.consoleUnsavedMessage ?? "You have changes that haven't been saved. If you leave now they will be lost.",
    confirmLabel: data.consoleUnsavedConfirm ?? "Discard changes",
    cancelLabel: data.consoleUnsavedCancel ?? "Keep editing",
    danger: true,
  }).then((confirmed) => {
    if (confirmed) discardChanges(forms);
    return confirmed;
  });
}

function plainLeavingLink(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return null;
  }
  const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!(link instanceof HTMLAnchorElement) || link._htmx?.initialized) return null;
  if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return null;
  if (link.closest("[data-bs-toggle], [data-console-dialog-close]")) return null;
  const url = new URL(link.href, window.location.href);
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const samePage = url.origin === window.location.origin && url.pathname === window.location.pathname && url.search === window.location.search;
  return samePage && url.hash ? null : link;
}

function onClick(event) {
  const link = plainLeavingLink(event);
  if (!link) return;
  const forms = dirtyForms().filter((form) => !form.contains(link));
  if (forms.length === 0) return;
  event.preventDefault();
  askToDiscard(forms).then((confirmed) => {
    if (confirmed) window.location.assign(link.href);
  });
}

// An htmx request that would replace a dirty form (and is not that form saving itself).
function onConfigRequest(event) {
  const { ctx } = event.detail;
  if (ctx.confirm || !(ctx.target instanceof Element)) return;
  const leaving = dirtyForms().filter(
    (form) => ctx.target.contains(form) && !form.contains(ctx.sourceElement) && form !== ctx.sourceElement,
  );
  if (leaving.length > 0) ctx.confirm = UNSAVED_QUESTION;
}

export function initUnsavedGuard() {
  snapshot();
  document.addEventListener("htmx:after:swap", () => snapshot());
  document.addEventListener("input", (event) => {
    const form = event.target instanceof Element ? event.target.closest(SELECTOR) : null;
    if (form) submitted.delete(form);
  });
  document.addEventListener("submit", (event) => {
    if (event.target instanceof HTMLFormElement && event.target.matches(SELECTOR)) submitted.add(event.target);
  });
  document.addEventListener("reset", (event) => {
    if (event.target instanceof HTMLFormElement) window.setTimeout(() => discardChanges([event.target]), 0);
  });
  document.addEventListener("click", onClick);
  document.addEventListener("htmx:config:request", onConfigRequest);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) document.querySelectorAll(SELECTOR).forEach((form) => submitted.delete(form));
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirtyForms().length === 0) return;
    event.preventDefault();
    event.returnValue = "";
  });
}
