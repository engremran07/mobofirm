// Dialog host: `#console-dialog` (a native <dialog class="modal">, see ui/modal.js) whose innerHTML is replaced by htmx
// (`hx-get` + `hx-target="#console-dialog"` + `hx-swap="innerHTML"`).
//
// Fragment contract (templates/console/components/dialog.html):
//   <div class="modal-dialog ..." data-console-dialog-content><div class="modal-content">...</div></div>
// A bare `<div data-console-dialog-content>` holding .modal-header/.modal-body/.modal-footer
// is wrapped in .modal-dialog > .modal-content here. Optional on the content root:
//   data-console-dialog-size="sm|lg|xl|fullscreen"   data-console-dialog-static (no ESC/backdrop close)
// Closing: any `[data-console-dialog-close]` button inside, the `console-dialog-close` event
// (HX-Trigger), or a swapped response that has no dialog content but carries
// `[data-console-dialog-close]` / `[data-console-popup-response]` / is empty.
// Forms in the dialog without their own hx-* attribute are upgraded to htmx posts that swap
// back into the dialog (opt out with data-console-dialog-plain).
// Staff dialogs (templates/admin/staff_dialog.html, apps.core.dialogs) arrive as a full
// `.modal[data-console-dialog]`: its `.modal-dialog` is adopted into the host (the host IS the
// modal), keeping `data-console-dialog-backdrop="static"`, `data-console-dialog-esc="block"`,
// `data-console-dialog-phrase="TEXT"` (submit stays disabled until the `confirmation` field
// matches; the server enforces it too) and `data-console-dialog-initial-focus` (a safe
// button such as Cancel wins over the first field; a submit button only when there are no
// fields). Their form re-renders in place with hx-select, so the swap target is remembered per
// request. HX-Trigger `dialog:close` closes; `dialog:done` is left for page listeners.
// Events fired on document after the modal finished showing / hiding (for tests and widgets):
// console-dialog-opened / console-dialog-closed. Requests to open/close come in as
// console-dialog-open {url} / console-dialog-close (HX-Trigger).
import htmx from "htmx.org";

import { renderIcons } from "../icons.js";
import { NativeModal as Modal } from "../ui/modal.js";
import { EVENTS, emit, ensureId, focusables, isVisible } from "../lib/util.js";
import { askToDiscard, dirtyForms } from "./unsaved.js";
import { handlePopupResponse } from "./related.js";

const HOST = "#console-dialog";
const SIZES = { sm: "modal-sm", lg: "modal-lg", xl: "modal-xl", fullscreen: "modal-fullscreen" };
const HX_ATTRIBUTES = ["hx-post", "hx-get", "hx-put", "hx-patch", "hx-delete"];

const PHRASE = "data-console-dialog-phrase";

let opener = null;
let loadedUrl = "";
let discardApproved = false;
const dialogRequests = new WeakSet();

const host = () => document.querySelector(HOST);
const isShown = (element) => element instanceof HTMLDialogElement && element.open;

// Moves the `.modal-dialog` of a staff dialog fragment up into the host.
function adoptStaffDialog(root) {
  const staff = root.querySelector(":scope > [data-console-dialog]");
  if (!staff) return;
  const dialog = staff.querySelector(":scope > .modal-dialog");
  if (!(dialog instanceof HTMLElement)) {
    staff.remove();
    return;
  }
  for (const name of ["data-console-dialog-backdrop", "data-console-dialog-esc", PHRASE]) {
    const value = staff.getAttribute(name);
    if (value !== null) dialog.setAttribute(name, value);
  }
  dialog.setAttribute("data-console-dialog-content", "");
  const described = staff.getAttribute("aria-describedby");
  if (described) root.setAttribute("aria-describedby", described);
  else root.removeAttribute("aria-describedby");
  // The host IS the dialog: it carries the staff-dialog hook for as long as one is shown.
  root.setAttribute("data-console-dialog", "");
  root.replaceChildren(dialog);
}

function syncPhrase(root) {
  const phrase = root.querySelector(`[${PHRASE}]`)?.getAttribute(PHRASE);
  if (!phrase) return;
  const field = root.querySelector('input[name="confirmation"]');
  const submit = root.querySelector('button[type="submit"]');
  if (field instanceof HTMLInputElement && submit instanceof HTMLButtonElement) {
    submit.disabled = field.value.trim() !== phrase;
  }
}

function wrapContent(root) {
  const content = root.querySelector("[data-console-dialog-content]");
  if (!content) return null;
  if (content.classList.contains("modal-dialog") || content.querySelector(".modal-dialog")) return content;
  const dialog = document.createElement("div");
  dialog.className = "modal-dialog modal-dialog-centered modal-dialog-scrollable";
  const size = SIZES[content.dataset.consoleDialogSize ?? ""];
  if (size) dialog.classList.add(size);
  content.classList.add("modal-content");
  content.replaceWith(dialog);
  dialog.append(content);
  return dialog;
}

function upgradeForms(root) {
  for (const form of root.querySelectorAll("form")) {
    if (form.hasAttribute("data-console-dialog-plain") || form.method === "dialog") continue;
    if (HX_ATTRIBUTES.some((name) => form.hasAttribute(name))) continue;
    const method = (form.getAttribute("method") ?? "get").toLowerCase() === "post" ? "hx-post" : "hx-get";
    form.setAttribute(method, form.getAttribute("action") || loadedUrl || window.location.pathname);
    if (!form.hasAttribute("hx-target")) form.setAttribute("hx-target", HOST);
    if (!form.hasAttribute("hx-swap")) form.setAttribute("hx-swap", "innerHTML");
    if (/multipart/i.test(form.enctype)) form.setAttribute("hx-encoding", "multipart/form-data");
    htmx.process(form);
  }
}

function label(root) {
  const title = root.querySelector(".modal-title, [data-console-dialog-title], h1, h2, h3");
  if (title) {
    root.setAttribute("aria-labelledby", ensureId(title, "console-dialog-title"));
    root.removeAttribute("aria-label");
  } else {
    root.removeAttribute("aria-labelledby");
    root.setAttribute("aria-label", "Dialog");
  }
}

function focusInitial(root) {
  const body = root.querySelector(".modal-body") ?? root;
  const marked = root.querySelector("[data-console-dialog-initial-focus]");
  const safeMarked = marked && !marked.matches('[type="submit"]') ? marked : null;
  const target =
    root.querySelector("[autofocus]") ??
    body.querySelector('[aria-invalid="true"], .is-invalid') ??
    safeMarked ??
    [...body.querySelectorAll("input:not([type=hidden]), select, textarea")].find(
      (field) => field.getClientRects().length > 0 && !field.disabled,
    ) ??
    marked ??
    root.querySelector(".modal-footer .btn-primary, .modal-footer .btn-danger") ??
    focusables(root)[0];
  if (target instanceof HTMLElement) target.focus({ preventScroll: true });
}

function present(root) {
  const content = root.querySelector("[data-console-dialog-content]");
  const escBlocked = Boolean(
    root.querySelector("[data-console-dialog-static], [data-console-dialog-esc='block']"),
  );
  const staticBackdrop =
    escBlocked ||
    Boolean(
      root.querySelector("[data-bs-backdrop='static']") ??
        (content?.getAttribute("data-console-dialog-backdrop") === "static" ? content : null),
    );
  label(root);
  upgradeForms(root);
  renderIcons(root);
  syncPhrase(root);
  if (isShown(root)) {
    focusInitial(root);
    return;
  }
  Modal.getInstance(root)?.dispose();
  const modal = Modal.getOrCreateInstance(root, {
    backdrop: staticBackdrop ? "static" : true,
    keyboard: !escBlocked,
    focus: true,
  });
  modal.show();
}

export function closeDialog() {
  const root = host();
  if (root && isShown(root)) Modal.getInstance(root)?.hide();
}

export function openDialog(url, source = document.activeElement) {
  opener = source instanceof HTMLElement ? source : null;
  return htmx.ajax("get", url, { target: HOST, swap: "innerHTML", source: opener ?? undefined });
}

function inHost(ctx, root) {
  return ctx.target instanceof Element && (ctx.target === root || root.contains(ctx.target));
}

function onBeforeRequest(event) {
  const { ctx } = event.detail;
  const root = host();
  if (!root || !inHost(ctx, root)) return;
  dialogRequests.add(ctx);
  loadedUrl = ctx.request.method === "GET" || ctx.target === root ? ctx.request.action : loadedUrl;
  if (!isShown(root) && ctx.sourceElement instanceof HTMLElement && !root.contains(ctx.sourceElement)) {
    opener = ctx.sourceElement;
  }
}

function onAfterSwap(event) {
  const { ctx } = event.detail;
  const root = host();
  if (!root || !(dialogRequests.has(ctx) || inHost(ctx, root))) return;
  adoptStaffDialog(root);
  const response = root.querySelector("[data-console-popup-response]");
  if (response) handlePopupResponse(response);
  const content = wrapContent(root);
  if (!content || response || root.querySelector(":scope > [data-console-dialog-close]")) {
    if (isShown(root)) discardApproved = true;
    closeDialog();
    if (!isShown(root)) root.replaceChildren();
    return;
  }
  present(root);
}

function onClick(event) {
  const closer = event.target instanceof Element ? event.target.closest("[data-console-dialog-close]") : null;
  const root = host();
  if (closer instanceof HTMLElement && root?.contains(closer) && closer.matches("button, a, [role=button]")) {
    event.preventDefault();
    closeDialog();
  }
}

function onHide(event) {
  const root = host();
  if (!root || event.target !== root) return;
  const forms = dirtyForms(root);
  if (discardApproved || forms.length === 0) {
    discardApproved = false;
    return;
  }
  event.preventDefault();
  askToDiscard(forms).then((confirmed) => {
    if (!confirmed) return;
    discardApproved = true;
    closeDialog();
  });
}

function onHidden(event) {
  const root = host();
  if (!root || event.target !== root) return;
  root.replaceChildren();
  root.removeAttribute("data-console-dialog");
  Modal.getInstance(root)?.dispose();
  discardApproved = false;
  // The opener may live in a menu that closed meanwhile (row actions): fall back to that menu's trigger.
  const target = opener?.isConnected && isVisible(opener) ? opener : opener?.closest("[data-ui-popover]")?.querySelector("[data-ui-trigger]");
  if (target instanceof HTMLElement) target.focus({ preventScroll: true });
  else document.getElementById("console-main")?.focus({ preventScroll: true });
  opener = null;
  emit(EVENTS.dialogClosed, {});
}

function onShown(event) {
  const root = host();
  if (!root || event.target !== root) return;
  focusInitial(root);
  emit(EVENTS.dialogOpened, {});
}

export function initDialogHost() {
  document.addEventListener("htmx:before:request", onBeforeRequest);
  document.addEventListener("htmx:after:swap", onAfterSwap);
  document.addEventListener("click", onClick);
  document.addEventListener("hide.ui.modal", onHide);
  document.addEventListener("hidden.ui.modal", onHidden);
  document.addEventListener("shown.ui.modal", onShown);
  document.addEventListener(EVENTS.dialogClose, () => {
    discardApproved = true;
    closeDialog();
  });
  document.addEventListener("dialog:close", () => {
    discardApproved = true;
    closeDialog();
  });
  document.addEventListener("input", (event) => {
    const root = host();
    if (root && event.target instanceof Node && root.contains(event.target)) syncPhrase(root);
  });
  document.addEventListener(EVENTS.dialogOpen, (event) => {
    const url = event.detail?.url;
    if (typeof url === "string") openDialog(url);
  });
}
