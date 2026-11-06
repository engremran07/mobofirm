function openDialog(dialog) {
  if (!(dialog instanceof HTMLDialogElement) || dialog.open) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
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
}

// App-owned confirmation dialogs (components/confirm_dialog.html).
export function initializeAppDialogs() {
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      openDialog(document.getElementById(opener.dataset.dialogOpen));
      return;
    }
    const closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }
    const dialog = event.target;
    if (dialog instanceof HTMLDialogElement && dialog.hasAttribute("data-app-dialog")) {
      // A click on the ::backdrop is dispatched to the dialog element itself.
      dialog.close();
    }
  });
}
