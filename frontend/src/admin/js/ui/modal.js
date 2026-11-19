// Native <dialog> modal: the console's one modal primitive (replaces Bootstrap's Modal JS).
//
// Markup: <dialog class="modal" ...><div class="modal-dialog"><div class="modal-content">..</div></div></dialog>. The `.modal*`
// classes are only layout/skin (styles/components/_dialog.scss); behaviour comes from the platform: showModal() makes the page
// inert, traps focus and restores it on close, and ::backdrop is the scrim.
//
// API (small, Bootstrap-shaped so call sites stay readable):
//   new NativeModal(dialog, { backdrop: true | "static", keyboard: true })   .show()  .hide()  .dispose()
//   NativeModal.getInstance(dialog) / NativeModal.getOrCreateInstance(dialog, options)
// Events dispatched on the dialog (they bubble): show.ui.modal, shown.ui.modal, hide.ui.modal (cancelable: preventDefault keeps it
// open, used by the unsaved-changes guard), hidden.ui.modal. State attributes: [open], data-open and the `show` class (kept so
// existing `.modal.show` selectors keep working).
// Dismiss controls: any [data-ui-dismiss="modal"] (or the legacy [data-bs-dismiss="modal"]) inside a dialog hides it.
// Escape goes through hide(); a click on the backdrop (the dialog element itself) hides unless backdrop is "static".
const instances = new WeakMap();
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const fire = (element, name, cancelable = false) => {
  const event = new CustomEvent(`${name}.ui.modal`, { bubbles: true, cancelable });
  element.dispatchEvent(event);
  return !event.defaultPrevented;
};

export class NativeModal {
  static getInstance(element) {
    return instances.get(element) ?? null;
  }

  static getOrCreateInstance(element, options = {}) {
    return NativeModal.getInstance(element) ?? new NativeModal(element, options);
  }

  constructor(element, options = {}) {
    this.element = element;
    this.options = { backdrop: true, keyboard: true, ...options };
    this.abort = new AbortController();
    instances.set(element, this);
    const { signal } = this.abort;
    element.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Tab") return;
        // Keep Tab inside the dialog: wrap at both ends instead of leaving for the browser chrome.
        const items = [...element.querySelectorAll(FOCUSABLE)].filter((item) => item.getClientRects().length > 0);
        if (items.length === 0) return;
        const first = items[0];
        const last = items.at(-1);
        if (event.shiftKey && (document.activeElement === first || !element.contains(document.activeElement))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      },
      { signal },
    );
    element.addEventListener(
      "keydown",
      (event) => {
        // Escape inside a search box would only clear it; the dialog still has to close.
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.preventDefault();
        if (this.options.keyboard) this.hide();
      },
      { signal },
    );
    element.addEventListener(
      "cancel",
      (event) => {
        event.preventDefault();
        if (this.options.keyboard) this.hide();
      },
      { signal },
    );
    element.addEventListener(
      "click",
      (event) => {
        if (event.target === element && this.options.backdrop === true) this.hide();
      },
      { signal },
    );
    element.addEventListener(
      "close",
      () => {
        element.classList.remove("show");
        element.removeAttribute("data-open");
        fire(element, "hidden");
      },
      { signal },
    );
  }

  show() {
    const { element } = this;
    if (element.open) return;
    if (!fire(element, "show", true)) return;
    element.showModal();
    element.classList.add("show");
    element.setAttribute("data-open", "");
    fire(element, "shown");
  }

  hide() {
    const { element } = this;
    if (!element.open) return;
    if (!fire(element, "hide", true)) return;
    // The state hooks drop immediately; the `close` event (a queued task) only reports it.
    element.classList.remove("show");
    element.removeAttribute("data-open");
    element.close();
  }

  dispose() {
    this.abort.abort();
    instances.delete(this.element);
  }
}

export function initModals() {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const dismiss = event.target.closest('[data-ui-dismiss="modal"], [data-bs-dismiss="modal"]');
    const dialog = dismiss?.closest("dialog");
    if (dialog) NativeModal.getInstance(dialog)?.hide();
  });
}
