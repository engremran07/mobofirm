// App-owned dialogs: the single replacement for the browser's native dialogs and
// the HTMX confirm attribute. Core owns only this shell; every concrete
// dialog lives in the app that owns the behaviour (docs/05-frontend/dialogs.md).
//
// Pieces:
//   * Alpine component `appDialog` (components/dialog.html, dialog_js_template.html)
//   * HTMX dialogs loaded into #dialog-slot (`dialog:close` / `dialog:done` events)
//   * Dialogs.confirm / confirmWithReason / open for JavaScript callers
//   * data-confirm-dialog and the htmx:confirm interception (the replacement for the
//     HTMX confirm attribute, which would use the browser's native prompt)
//   * the unsaved-changes guard for forms marked `data-unsaved-guard`

const SLOT_ID = "dialog-slot";
const JS_TEMPLATE_ID = "app-dialog-template";
const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex=\"-1\"])";
const HX_REQUEST = "[hx-get],[hx-post],[hx-put],[hx-patch],[hx-delete]";

const openers = new WeakMap();
const dirtyForms = new Set();
let pendingOpener = null;
let bypassSubmit = false;
let jsDialogCount = 0;

function initialFocus(dialog) {
  const field = [
    ...dialog.querySelectorAll(
      ".app-dialog__body :is(input:not([type=hidden]), textarea, select)",
    ),
  ].find((element) => element.getClientRects().length > 0);
  // After a server-side validation error the first invalid field gets focus.
  const invalid = dialog.querySelector(
    '.app-dialog__body :is([aria-invalid="true"], .is-invalid)',
  );
  const target =
    dialog.querySelector("[autofocus]") ??
    invalid ??
    field ??
    dialog.querySelector("[data-dialog-initial-focus]");
  if (target instanceof HTMLElement) target.focus();
}

export function showAppDialog(dialog, opener) {
  if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
  openers.set(dialog, pendingOpener ?? opener ?? document.activeElement);
  pendingOpener = null;
  dialog.showModal();
  initialFocus(dialog);
}

function restoreFocus(dialog) {
  const opener = openers.get(dialog);
  openers.delete(dialog);
  if (opener instanceof HTMLElement && opener.isConnected) {
    opener.focus({ preventScroll: true });
  } else {
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }
}

function pruneDirtyForms() {
  for (const form of dirtyForms) {
    if (!form.isConnected) dirtyForms.delete(form);
  }
}

function hasUnsavedChanges() {
  pruneDirtyForms();
  return dirtyForms.size > 0;
}

function hasDirtyFormIn(root) {
  return [...dirtyForms].some((form) => root.contains(form));
}

function jsDialog(options) {
  const template = document.getElementById(JS_TEMPLATE_ID);
  if (!(template instanceof HTMLTemplateElement) || !template.content.firstElementChild) {
    throw new Error("The app dialog template (#app-dialog-template) is missing from the page.");
  }
  const dialog = template.content.firstElementChild.cloneNode(true);
  jsDialogCount += 1;
  const suffix = `-${jsDialogCount}`;
  for (const element of dialog.querySelectorAll("[id]")) element.id += suffix;
  for (const label of dialog.querySelectorAll("label[for]")) {
    label.htmlFor += suffix;
  }
  dialog.setAttribute("aria-labelledby", `app-js-dialog-title${suffix}`);
  dialog.setAttribute("aria-describedby", `app-js-dialog-message${suffix}`);
  dialog.setAttribute("data-dialog-js", "");

  dialog.querySelector("[data-dialog-title]").textContent = options.title ?? "Please confirm";
  const message = dialog.querySelector("[data-dialog-message]");
  if (options.message) {
    message.textContent = options.message;
  } else {
    message.remove();
    dialog.removeAttribute("aria-describedby");
  }
  const confirmButton = dialog.querySelector("[data-dialog-confirm]");
  const cancelButton = dialog.querySelector("[data-dialog-cancel]");
  if (options.confirmLabel) confirmButton.textContent = options.confirmLabel;
  if (options.cancelLabel) cancelButton.textContent = options.cancelLabel;
  if (options.danger) {
    dialog.classList.add("app-dialog--danger");
    dialog.dataset.dialogBackdrop = "static";
    confirmButton.classList.replace("btn-primary", "btn-danger");
  } else {
    cancelButton.removeAttribute("data-dialog-initial-focus");
    confirmButton.setAttribute("data-dialog-initial-focus", "");
  }
  if (options.reason) {
    const wrapper = dialog.querySelector("[data-dialog-reason]");
    wrapper.hidden = false;
    dialog.querySelector("[data-dialog-reason-label]").textContent =
      options.reason.label ?? "Reason";
    dialog.querySelector("textarea").required = Boolean(options.reason.required);
    dialog.querySelector("[data-dialog-reason-error]").textContent =
      options.reason.requiredMessage ?? "Enter a reason to continue.";
  }
  if (options.phrase) {
    dialog.dataset.dialogPhrase = options.phrase;
    dialog.querySelector("[data-dialog-phrase-field]").hidden = false;
    dialog.querySelector("[data-dialog-phrase-label]").textContent =
      options.phraseLabel ?? `Type “${options.phrase}” to confirm`;
  }
  return dialog;
}

function ask(options, opener) {
  return new Promise((resolve, reject) => {
    let dialog;
    try {
      dialog = jsDialog(options);
    } catch (error) {
      reject(error);
      return;
    }
    dialog.addEventListener("close", () => {
      const confirmed = dialog.returnValue === "confirm";
      const reason = dialog.querySelector("textarea[name=reason]");
      resolve({ confirmed, reason: confirmed && reason ? reason.value.trim() : "" });
    });
    document.body.append(dialog);
    showAppDialog(dialog, opener);
  });
}

// Replaces the browser's native confirm: resolves true only on an explicit confirm.
export async function confirmDialog(options = {}) {
  const outcome = await ask(options, options.opener);
  return outcome.confirmed;
}

// Like confirmDialog but asks for a reason; resolves the trimmed reason, or null if cancelled.
export async function confirmWithReason(options = {}) {
  const outcome = await ask(
    { ...options, reason: { label: "Reason", ...(options.reason ?? {}) } },
    options.opener,
  );
  return outcome.confirmed ? outcome.reason : null;
}

export function openDialogFragment(url, opener) {
  pendingOpener = opener ?? document.activeElement;
  return window.htmx.ajax("get", url, { target: `#${SLOT_ID}`, swap: "innerHTML" });
}

export function closeDialogs() {
  for (const dialog of document.querySelectorAll("dialog[data-app-dialog][open]")) {
    dialog.close("cancel");
  }
}

export const Dialogs = {
  confirm: confirmDialog,
  confirmWithReason,
  open: openDialogFragment,
  close: closeDialogs,
};

function discardOptions(form) {
  const data = form?.dataset ?? {};
  return {
    title: data.unsavedTitle ?? "Discard unsaved changes?",
    message:
      data.unsavedMessage ?? "You have changes that have not been saved. If you leave now they will be lost.",
    confirmLabel: data.unsavedConfirm ?? "Discard changes",
    cancelLabel: data.unsavedCancel ?? "Keep editing",
    danger: true,
  };
}

function firstDirtyForm() {
  pruneDirtyForms();
  return dirtyForms.values().next().value;
}

async function confirmDiscard() {
  const confirmed = await confirmDialog(discardOptions(firstDirtyForm()));
  if (confirmed) dirtyForms.clear();
  return confirmed;
}

function confirmOptions(source, question) {
  const data = source?.dataset ?? {};
  return {
    title: data.confirmTitle ?? "Please confirm",
    message: data.confirmDialog || question || "",
    confirmLabel: data.confirmLabel,
    cancelLabel: data.confirmCancel,
    danger: data.confirmDanger !== undefined && data.confirmDanger !== "false",
    opener: source instanceof HTMLElement ? source : undefined,
  };
}

function isPlainClick(event) {
  return (
    !event.defaultPrevented &&
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  );
}

function leavesPage(link) {
  if (link.target && link.target !== "_self") return false;
  if (link.hasAttribute("download")) return false;
  const url = new URL(link.href, window.location.href);
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  const samePage =
    url.origin === window.location.origin &&
    url.pathname === window.location.pathname &&
    url.search === window.location.search;
  return !(samePage && url.hash);
}

function onClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || !isPlainClick(event)) return;
  const opener = target.closest("[data-dialog-open]");
  if (opener) {
    showAppDialog(document.getElementById(opener.dataset.dialogOpen), opener);
    return;
  }
  const link = target.closest("a[href]");
  if (!link || link.closest(HX_REQUEST) || link.closest("[hx-boost]:not([hx-boost=false])")) {
    return;
  }
  const source = link.closest("[data-confirm-dialog]");
  if (!leavesPage(link) || (!source && !hasUnsavedChanges())) return;
  event.preventDefault();
  (async () => {
    if (source && !(await confirmDialog(confirmOptions(source)))) return;
    if (hasUnsavedChanges() && !(await confirmDiscard())) return;
    window.location.assign(link.href);
  })();
}

function onSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.method === "dialog" || bypassSubmit) return;
  const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
  if (form.matches(HX_REQUEST) || submitter?.matches(HX_REQUEST)) {
    // htmx issues these; the htmx:confirm handler asks. A guarded form that submits is saved.
    if (!submitter?.closest("[data-confirm-dialog]") && !form.closest("[data-confirm-dialog]")) {
      dirtyForms.delete(form);
    }
    return;
  }
  const source = submitter?.closest("[data-confirm-dialog]") ?? form.closest("[data-confirm-dialog]");
  const leaving = hasUnsavedChanges() && !dirtyForms.has(form);
  if (!source && !leaving) {
    dirtyForms.delete(form);
    return;
  }
  event.preventDefault();
  (async () => {
    if (source && !(await confirmDialog(confirmOptions(source)))) return;
    if (leaving && !(await confirmDiscard())) return;
    dirtyForms.delete(form);
    bypassSubmit = true;
    try {
      form.requestSubmit(submitter ?? undefined);
    } finally {
      bypassSubmit = false;
    }
  })();
}

function onHtmxConfirm(event) {
  const { elt, target, issueRequest, question } = event.detail;
  const submitter = event.detail.triggeringEvent?.submitter;
  const source =
    (submitter instanceof Element ? submitter.closest("[data-confirm-dialog]") : null) ??
    elt.closest("[data-confirm-dialog]");
  const discards =
    hasUnsavedChanges() &&
    target instanceof Element &&
    [...dirtyForms].some((form) => !elt.closest("form")?.isSameNode(form) && target.contains(form));
  if (!source && !question && !discards) return;
  event.preventDefault();
  (async () => {
    if ((source || question) && !(await confirmDialog(confirmOptions(source, question)))) return;
    if (discards && !(await confirmDiscard())) return;
    issueRequest(true);
  })();
}

function markDirty(event) {
  const form =
    event.target instanceof Element ? event.target.closest("form[data-unsaved-guard]") : null;
  if (form) dirtyForms.add(form);
}

// Plain (non-HTMX) form posts from a dialog show the loading state and cannot be sent
// twice. HTMX forms get .htmx-request from HTMX itself.
function onDialogFormSubmit(event) {
  const form = event.target;
  if (
    event.defaultPrevented ||
    !(form instanceof HTMLFormElement) ||
    form.method === "dialog" ||
    !form.closest("dialog[data-app-dialog]") ||
    form.matches(HX_REQUEST)
  ) {
    return;
  }
  if (form.hasAttribute("data-submitting")) {
    event.preventDefault();
    return;
  }
  form.setAttribute("data-submitting", "");
  form.setAttribute("aria-busy", "true");
  // A response that does not navigate (a download, a network failure) must not leave
  // the dialog stuck in its loading state.
  window.setTimeout(clearSubmitting, 20000);
}

function clearSubmitting() {
  for (const form of document.querySelectorAll("form[data-submitting]")) {
    form.removeAttribute("data-submitting");
    form.removeAttribute("aria-busy");
  }
}

function onDialogClose(event) {
  const origin =
    event.target instanceof Element ? event.target.closest("dialog[data-app-dialog]") : null;
  const dialogs = origin
    ? [origin]
    : [...(document.getElementById(SLOT_ID)?.querySelectorAll("dialog[open]") ?? [])];
  for (const dialog of dialogs) {
    for (const form of dirtyForms) {
      if (dialog.contains(form)) dirtyForms.delete(form);
    }
    dialog.close("done");
  }
}

// Native modals let Tab escape to the browser UI; keep it cycling inside the dialog.
function trapTab(event, dialog) {
  if (event.key !== "Tab") return;
  const focusable = [...dialog.querySelectorAll(FOCUSABLE)].filter(
    (element) => element.getClientRects().length > 0,
  );
  if (focusable.length === 0) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || active === dialog)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

// Hand-written dialogs that carry data-app-dialog but not the Alpine component still get
// the shared behaviour: [data-dialog-close], focus trap, focus restore and backdrop close.
const PLAIN_DIALOG = "dialog[data-app-dialog]:not([x-data])";

function plainDialogOf(node) {
  return node instanceof Element ? node.closest(PLAIN_DIALOG) : null;
}

function initializePlainDialogs() {
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const closer = target?.closest("[data-dialog-close]");
    const owner = closer?.closest("dialog");
    if (owner instanceof HTMLDialogElement) owner.close("cancel");
    const dialog = event.target instanceof HTMLDialogElement ? event.target : null;
    if (dialog?.matches(PLAIN_DIALOG) && dialog.dataset.dialogBackdrop !== "static") {
      const rect = dialog.getBoundingClientRect();
      const outside =
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom;
      if (outside) dialog.close("cancel");
    }
  });
  document.addEventListener("keydown", (event) => {
    const dialog = plainDialogOf(event.target);
    if (dialog) trapTab(event, dialog);
  });
  // `close` does not bubble, so listen in the capture phase.
  document.addEventListener(
    "close",
    (event) => {
      if (event.target instanceof HTMLDialogElement && event.target.matches(PLAIN_DIALOG)) {
        restoreFocus(event.target);
      }
    },
    true,
  );
}

export function registerDialogs(Alpine) {
  Alpine.data("appDialog", () => ({
    typed: "",
    phrase: "",
    pressedOnBackdrop: false,
    init() {
      const dialog = this.$root;
      this.phrase = dialog.dataset.dialogPhrase ?? "";
      if (dialog.hasAttribute("data-dialog-auto-open")) showAppDialog(dialog);
    },
    get submitBlocked() {
      return this.phrase !== "" && this.typed.trim() !== this.phrase;
    },
    onInput(event) {
      if (event.target.name === "confirmation") this.typed = event.target.value;
    },
    trapFocus(event) {
      trapTab(event, this.$root);
    },
    // Own the validation message instead of the browser's native tooltip.
    validate(event) {
      const form = event.target;
      if (event.submitter?.value !== "confirm") return;
      const reason = form.querySelector("textarea[name=reason]");
      const error = form.querySelector("[data-dialog-reason-error]");
      if (!reason || !error || reason.closest("[hidden]")) return;
      const missing = reason.required && reason.value.trim() === "";
      error.hidden = !missing;
      reason.setAttribute("aria-invalid", missing ? "true" : "false");
      if (missing) {
        event.preventDefault();
        reason.focus();
      }
    },
    rememberPress(event) {
      this.pressedOnBackdrop = event.target === this.$root;
    },
    onBackdrop(event) {
      const dialog = this.$root;
      const pressed = this.pressedOnBackdrop;
      this.pressedOnBackdrop = false;
      if (event.target !== dialog || !pressed) return;
      if (dialog.dataset.dialogBackdrop !== "static") this.requestClose();
    },
    onCancel(event) {
      event.preventDefault();
      if (this.$root.dataset.dialogEsc !== "block") this.requestClose();
    },
    async requestClose() {
      const dialog = this.$root;
      if (hasDirtyFormIn(dialog) && !(await confirmDiscard())) return;
      dialog.close("cancel");
    },
    onClosed() {
      const dialog = this.$root;
      restoreFocus(dialog);
      if (dialog.hasAttribute("data-dialog-js") || dialog.parentElement?.id === SLOT_ID) {
        dialog.remove();
      }
    },
  }));
}

// Single registration point for the document-level behaviour.
export function initializeAppDialogs() {
  initializePlainDialogs();
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit, true);
  document.addEventListener("submit", onDialogFormSubmit);
  // Back/forward cache restores the page with a stale loading state.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) clearSubmitting();
  });
  document.addEventListener("input", markDirty);
  document.addEventListener("change", markDirty);
  document.addEventListener("reset", (event) => {
    if (event.target instanceof HTMLFormElement) dirtyForms.delete(event.target);
  });
  document.addEventListener("htmx:confirm", onHtmxConfirm);
  document.addEventListener("dialog:close", onDialogClose);
  document.addEventListener("htmx:afterRequest", (event) => {
    if (!event.detail.successful) pendingOpener = null;
  });
  document.addEventListener("htmx:beforeSwap", (event) => {
    const target = event.detail.target;
    if (target instanceof HTMLDialogElement && openers.has(target)) {
      pendingOpener = openers.get(target);
    }
  });
  window.Dialogs = Dialogs;
  // The one browser-mandated fallback: a tab close, refresh or address-bar navigation
  // cannot show app UI, so the browser's generic prompt is requested only while a
  // `data-unsaved-guard` form has unsaved edits. In-app navigation uses the dialog above.
  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

export function initializeDeviceRegistrationDialog() {
  const dialog = document.querySelector("[data-device-registration-dialog]");
  if (!(dialog instanceof HTMLDialogElement) || typeof dialog.showModal !== "function") {
    return;
  }
  // The server renders it open (non-modal) so it still shows without JavaScript;
  // reopen as modal so the browser supplies the backdrop, focus trap and Esc.
  if (dialog.open) dialog.close();
  dialog.showModal();
  // Esc or "Not now" simply closes it; the prompt is shown again on the next page
  // until the visitor decides, and nothing stays on screen to block the page.
  dialog.addEventListener("close", () => {
    document.getElementById("main-content")?.focus({ preventScroll: true });
  });
  makeDialogDraggable(dialog, dialog.querySelector("[data-dialog-drag-handle]"));
}

// Drag a centred modal by its header. The offset is applied through CSS custom
// properties (`translate`) and clamped so the header can never leave the viewport.
function makeDialogDraggable(dialog, handle) {
  if (!(handle instanceof HTMLElement)) return;
  let start = null;
  const offset = { x: 0, y: 0 };
  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("a, button, input, select, textarea")) return;
    const box = dialog.getBoundingClientRect();
    start = { pointerX: event.clientX, pointerY: event.clientY, box, x: offset.x, y: offset.y };
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("is-dragging");
  });
  handle.addEventListener("pointermove", (event) => {
    if (!start) return;
    const { box } = start;
    const margin = 8;
    const dx = clamp(event.clientX - start.pointerX, margin - box.left, window.innerWidth - margin - box.right);
    const dy = clamp(event.clientY - start.pointerY, margin - box.top, window.innerHeight - margin - box.top - 48);
    offset.x = start.x + dx;
    offset.y = start.y + dy;
    dialog.style.setProperty("--dialog-drag-x", `${offset.x}px`);
    dialog.style.setProperty("--dialog-drag-y", `${offset.y}px`);
  });
  const stop = (event) => {
    if (!start) return;
    start = null;
    handle.classList.remove("is-dragging");
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
}
