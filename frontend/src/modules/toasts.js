// Toasts: transient, non-blocking feedback. One region (#toast-region, a polite live
// region rendered by base.html) hosts every toast. Sources:
//   * Django messages (includes/messages.html) are converted on load and after swaps
//   * HTMX failures (modules/feedback.js)
//   * the server: an `HX-Trigger: {"toast": {"message": "Saved", "level": "success"}}` header
//   * JavaScript: import { showToast } or dispatch `mf:toast` on document
// Text is always set with textContent, never as markup.
import { renderIcons } from "./icons.js";

const REGION_SELECTOR = "[data-toast-region]";
const MAX_VISIBLE = 4;
const LEVELS = {
  success: { icon: "circle-check", title: "Success", role: "status", duration: 5000 },
  info: { icon: "info", title: "Note", role: "status", duration: 6000 },
  warning: { icon: "triangle-alert", title: "Warning", role: "alert", duration: 9000 },
  danger: { icon: "circle-x", title: "Something went wrong", role: "alert", duration: 10000 },
};

function levelFor(name) {
  return LEVELS[name] ? name : "info";
}

function dismiss(toast) {
  if (!toast.isConnected || toast.classList.contains("is-leaving")) return;
  toast.classList.add("is-leaving");
  // Reduced motion collapses the transition, so fall back to a timer as well.
  const remove = () => toast.remove();
  toast.addEventListener("transitionend", remove, { once: true });
  window.setTimeout(remove, 400);
}

function build(level, title, message, duration) {
  const spec = LEVELS[level];
  const toast = document.createElement("div");
  toast.className = `toast-ui toast-ui--${level}`;
  toast.setAttribute("role", spec.role);
  toast.dataset.toast = level;
  toast.style.setProperty("--toast-duration", `${duration}ms`);

  const icon = document.createElement("span");
  icon.className = "toast-ui__icon";
  const glyph = document.createElement("i");
  glyph.dataset.lucide = spec.icon;
  glyph.setAttribute("aria-hidden", "true");
  icon.append(glyph);

  const heading = document.createElement("p");
  heading.className = "toast-ui__title";
  heading.textContent = title;
  const body = document.createElement("p");
  body.className = "toast-ui__message";
  body.textContent = message;

  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-ui__close";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";
  close.addEventListener("click", () => dismiss(toast));

  const bar = document.createElement("span");
  bar.className = "toast-ui__bar";
  bar.setAttribute("aria-hidden", "true");

  toast.append(icon, heading, close, body, bar);
  return toast;
}

export function showToast(options = {}) {
  const region = document.querySelector(REGION_SELECTOR);
  const message = String(options.message ?? "").trim();
  if (!region || !message) return null;
  const level = levelFor(options.level);
  const spec = LEVELS[level];

  // A repeated identical toast refreshes the existing one instead of stacking.
  for (const existing of region.querySelectorAll(".toast-ui")) {
    if (
      existing.dataset.toast === level &&
      existing.querySelector(".toast-ui__message")?.textContent === message
    ) {
      existing.remove();
    }
  }
  while (region.children.length >= MAX_VISIBLE) region.firstElementChild.remove();

  const duration = Number(options.duration) > 0 ? Number(options.duration) : spec.duration;
  const toast = build(level, options.title ?? spec.title, message, duration);
  region.append(toast);
  renderIcons();

  let timer = window.setTimeout(() => dismiss(toast), duration);
  // Hovering or focusing a toast pauses its timer so it can be read.
  const pause = () => {
    window.clearTimeout(timer);
    toast.querySelector(".toast-ui__bar")?.style.setProperty("animation-play-state", "paused");
  };
  const resume = () => {
    toast.querySelector(".toast-ui__bar")?.style.setProperty("animation-play-state", "running");
    timer = window.setTimeout(() => dismiss(toast), 2500);
  };
  toast.addEventListener("mouseenter", pause);
  toast.addEventListener("mouseleave", resume);
  toast.addEventListener("focusin", pause);
  toast.addEventListener("focusout", resume);
  return toast;
}

const FLASH_LEVELS = new Set(["success", "info", "warning", "danger"]);

// Turns server-rendered Django messages into toasts and removes the static alerts.
export function convertFlashMessages(root = document) {
  for (const container of root.querySelectorAll("[data-flash-messages]")) {
    for (const flash of container.querySelectorAll("[data-flash]")) {
      const level = FLASH_LEVELS.has(flash.dataset.flash) ? flash.dataset.flash : "info";
      showToast({ level, message: flash.textContent });
    }
    container.remove();
  }
}

export function initializeToasts() {
  convertFlashMessages();
  document.addEventListener("toast", (event) => {
    if (event.detail && typeof event.detail === "object") showToast(event.detail);
  });
  document.addEventListener("mf:toast", (event) => showToast(event.detail));
  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.target instanceof Element ? event.target : document;
    convertFlashMessages(target);
  });
  document.addEventListener("htmx:oobAfterSwap", () => convertFlashMessages());
}
