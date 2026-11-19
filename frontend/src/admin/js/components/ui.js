// Small document-level behaviours that need no component of their own.
//   [data-console-dismiss]                  removes its .c-alert / [data-console-dismissible]
//   [data-console-autodismiss="<ms>"]       removes the alert after the delay (paused while hovered/focused)
//   [data-console-copy="text"]              copies the text; or [data-console-copy-target="#id"] copies that
//                                           element's value/text. Feedback: toast + data-copied on the button.
import { announce } from "../lib/util.js";
import { showToast } from "./toasts.js";

const DISMISSIBLE = ".c-alert, [data-console-dismissible]";
const COPIED_MS = 1800;

function copyText(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "visually-hidden";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (ok) resolve();
    else reject(new Error("copy failed"));
  });
}

function sourceText(button) {
  const direct = button.dataset.consoleCopy;
  if (direct) return direct;
  const target = document.querySelector(button.dataset.consoleCopyTarget ?? "");
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return target.value;
  return target?.textContent?.trim() ?? "";
}

function onClick(event) {
  if (!(event.target instanceof Element)) return;
  const dismiss = event.target.closest("[data-console-dismiss]");
  if (dismiss) {
    event.preventDefault();
    const alert = dismiss.closest(DISMISSIBLE);
    alert?.remove();
    announce("Dismissed");
    return;
  }
  const copy = event.target.closest("[data-console-copy], [data-console-copy-target]");
  if (!(copy instanceof HTMLElement)) return;
  event.preventDefault();
  const text = sourceText(copy);
  if (!text) return;
  copyText(text).then(
    () => {
      copy.setAttribute("data-copied", "");
      window.setTimeout(() => copy.removeAttribute("data-copied"), COPIED_MS);
      showToast({ level: "success", message: copy.dataset.consoleCopyMessage ?? "Copied to the clipboard." });
    },
    () => showToast({ level: "warning", message: "Couldn't copy automatically. Select the text and copy it instead." }),
  );
}

function armAutodismiss() {
  for (const alert of document.querySelectorAll("[data-console-autodismiss]:not([data-armed])")) {
    alert.setAttribute("data-armed", "");
    const delay = Number(alert.getAttribute("data-console-autodismiss"));
    if (!(delay > 0)) continue;
    let timer = window.setTimeout(() => alert.remove(), delay);
    alert.addEventListener("pointerenter", () => window.clearTimeout(timer));
    alert.addEventListener("pointerleave", () => {
      timer = window.setTimeout(() => alert.remove(), delay);
    });
  }
}

export function initUi() {
  document.addEventListener("click", onClick);
  armAutodismiss();
  document.addEventListener("htmx:after:swap", armAutodismiss);
}
