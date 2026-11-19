// App-owned confirmation modal: the single replacement for window.confirm / alert.
//   confirmDialog({title, message, confirmLabel, cancelLabel, danger, opener}) -> Promise<boolean>
// Also wires `[data-console-confirm="Question?"]` on any button, link or submit button:
// the click is paused, the dialog asks, and the original action continues only on confirm.
// Optional copy: data-console-confirm-title|-label|-cancel, data-console-confirm-danger.
import { iconPlaceholder, renderIcons } from "../icons.js";
import { NativeModal as Modal } from "../ui/modal.js";
import { nextId } from "../lib/util.js";

function build(options) {
  const id = nextId("console-confirm");
  const root = document.createElement("dialog");
  root.className = "modal";
  root.setAttribute("data-console-confirm-dialog", "");
  root.setAttribute("aria-labelledby", `${id}-title`);
  root.setAttribute("aria-describedby", `${id}-message`);

  const dialog = document.createElement("div");
  dialog.className = "modal-dialog modal-dialog-centered";
  const content = document.createElement("div");
  content.className = "modal-content";

  const header = document.createElement("div");
  header.className = "modal-header";
  const title = document.createElement("h2");
  title.className = "modal-title";
  title.id = `${id}-title`;
  title.textContent = options.title ?? "Please confirm";
  const close = document.createElement("button");
  close.type = "button";
  close.className = "c-icon-btn";
  close.setAttribute("data-ui-dismiss", "modal");
  close.setAttribute("aria-label", "Close");
  const closeIcon = iconPlaceholder("x");
  closeIcon.className = "c-icon";
  close.append(closeIcon);
  header.append(title, close);

  const body = document.createElement("div");
  body.className = "modal-body";
  const message = document.createElement("p");
  message.id = `${id}-message`;
  message.className = "mb-0";
  message.textContent = options.message ?? "";
  body.append(message);

  const footer = document.createElement("div");
  footer.className = "modal-footer";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn btn-outline-secondary";
  cancel.setAttribute("data-ui-dismiss", "modal");
  cancel.textContent = options.cancelLabel ?? "Cancel";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = `btn ${options.danger ? "btn-danger" : "btn-primary"}`;
  confirm.setAttribute("data-console-confirm-ok", "");
  confirm.textContent = options.confirmLabel ?? "Confirm";
  footer.append(cancel, confirm);

  content.append(header, body, footer);
  dialog.append(content);
  root.append(dialog);
  return { root, confirm, cancel };
}

export function confirmDialog(options = {}) {
  return new Promise((resolve) => {
    const { root, confirm, cancel } = build(options);
    const opener = options.opener instanceof HTMLElement ? options.opener : document.activeElement;
    let confirmed = false;
    document.body.append(root);
    renderIcons(root);
    const modal = new Modal(root, { backdrop: options.danger ? "static" : true });
    confirm.addEventListener("click", () => {
      confirmed = true;
      modal.hide();
    });
    root.addEventListener("shown.ui.modal", () => (options.danger ? cancel : confirm).focus());
    root.addEventListener("hidden.ui.modal", () => {
      modal.dispose();
      root.remove();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
      resolve(confirmed);
    });
    modal.show();
  });
}

let bypass = null;

function onClick(event) {
  const source = event.target instanceof Element ? event.target.closest("[data-console-confirm]") : null;
  if (!(source instanceof HTMLElement) || source === bypass) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const data = source.dataset;
  confirmDialog({
    title: data.consoleConfirmTitle,
    message: data.consoleConfirm,
    confirmLabel: data.consoleConfirmLabel,
    cancelLabel: data.consoleConfirmCancel,
    danger: data.consoleConfirmDanger !== undefined && data.consoleConfirmDanger !== "false",
    opener: source,
  }).then((confirmed) => {
    if (!confirmed || !source.isConnected) return;
    bypass = source;
    try {
      source.click();
    } finally {
      bypass = null;
    }
  });
}

export function initConfirm() {
  // Capture phase: runs before htmx/Bootstrap/link handlers on the same element.
  document.addEventListener("click", onClick, true);
}
