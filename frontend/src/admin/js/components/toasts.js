// Toast stack: transient, non-blocking feedback in the polite live region
// `[data-console-toasts]` (#console-toasts).
//
// Sources: server-rendered `[data-console-toast]` messages (auto-armed, optional
// data-console-toast-timeout in ms, 0 = sticky), `HX-Trigger: {"console-toast": {...}}`
// (alias `toast`), and JavaScript `window.mobofirmConsole.toast({...})`. Text is only ever
// written with textContent.
//
// JS-created toasts clone `<template data-console-toast-template>` when present: the
// template root is the toast; hooks [data-console-toast-message], [data-console-toast-title],
// [data-console-toast-icon], [data-console-toast-close]. Without a template a default
// c-toast structure is built.
import { iconPlaceholder, renderIcons } from "../icons.js";

const MAX_VISIBLE = 4;
const LEVELS = {
  success: { icon: "circle-check", title: "Done", role: "status", duration: 5000 },
  info: { icon: "info", title: "Note", role: "status", duration: 6000 },
  warning: { icon: "triangle-alert", title: "Heads up", role: "alert", duration: 9000 },
  danger: { icon: "circle-x", title: "Something went wrong", role: "alert", duration: 10000 },
};
const ALIASES = { error: "danger", warn: "warning", debug: "info", message: "info" };

const levelOf = (name) => {
  const level = ALIASES[name] ?? name;
  return LEVELS[level] ? level : "info";
};

function region() {
  let host = document.querySelector("[data-console-toasts]");
  if (!host) {
    host = document.createElement("div");
    host.id = "console-toasts";
    host.className = "c-toast-region";
    host.setAttribute("data-console-toasts", "");
    host.setAttribute("role", "region");
    host.setAttribute("aria-label", "Notifications");
    document.body.append(host);
  }
  return host;
}

export function dismissToast(toast) {
  if (!toast.isConnected || toast.hasAttribute("data-leaving")) return;
  toast.setAttribute("data-leaving", "");
  toast.classList.add("is-leaving");
  const remove = () => toast.remove();
  toast.addEventListener("transitionend", remove, { once: true });
  window.setTimeout(remove, 250);
}

// Starts the auto-dismiss timer; hovering or focusing a toast pauses it so it can be read.
function arm(toast, fallbackMs) {
  if (toast.hasAttribute("data-armed")) return;
  toast.setAttribute("data-armed", "");
  const attr = toast.getAttribute("data-console-toast-timeout");
  const duration = attr === null ? fallbackMs : Number(attr);
  if (!(duration > 0)) return;
  let remaining = duration;
  let startedAt = 0;
  let timer = 0;
  const start = () => {
    startedAt = Date.now();
    timer = window.setTimeout(() => dismissToast(toast), remaining);
  };
  const pause = () => {
    window.clearTimeout(timer);
    remaining = Math.max(1000, remaining - (Date.now() - startedAt));
  };
  toast.addEventListener("pointerenter", pause);
  toast.addEventListener("pointerleave", start);
  toast.addEventListener("focusin", pause);
  toast.addEventListener("focusout", start);
  start();
}

function setIcon(slot, name) {
  const placeholder = iconPlaceholder(name);
  if (slot.matches("i, svg")) {
    placeholder.setAttribute("class", slot.getAttribute("class") ?? "");
    placeholder.setAttribute("data-console-toast-icon", "");
    slot.replaceWith(placeholder);
  } else {
    slot.replaceChildren(placeholder);
  }
}

function fromTemplate(template, { level, title, message }) {
  const toast = template.content.firstElementChild?.cloneNode(true);
  if (!(toast instanceof HTMLElement)) return null;
  toast.className = toast.className.replace(/c-toast--\w+/g, "").trim();
  toast.classList.add(`c-toast--${level}`);
  const titleSlot = toast.querySelector("[data-console-toast-title]");
  if (titleSlot) titleSlot.textContent = title;
  const messageSlot = toast.querySelector("[data-console-toast-message]");
  if (messageSlot) messageSlot.textContent = message;
  const iconSlot = toast.querySelector("[data-console-toast-icon]");
  if (iconSlot) setIcon(iconSlot, LEVELS[level].icon);
  return toast;
}

function defaultToast({ level, title, message }) {
  const toast = document.createElement("div");
  toast.className = `c-toast c-toast--${level}`;
  const icon = document.createElement("span");
  icon.className = "c-toast__icon";
  icon.append(iconPlaceholder(LEVELS[level].icon));
  const body = document.createElement("div");
  body.className = "c-toast__body";
  const heading = document.createElement("strong");
  heading.setAttribute("data-console-toast-title", "");
  heading.textContent = title;
  const text = document.createElement("span");
  text.setAttribute("data-console-toast-message", "");
  text.textContent = message;
  body.append(heading, " ", text);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "c-toast__close";
  close.setAttribute("data-console-toast-close", "");
  close.setAttribute("aria-label", "Dismiss notification");
  close.append(iconPlaceholder("x"));
  toast.append(icon, body, close);
  return toast;
}

export function showToast(options = {}) {
  const message = String(options.message ?? "").trim();
  if (!message) return null;
  const level = levelOf(options.level);
  const spec = LEVELS[level];
  const content = { level, message, title: String(options.title ?? spec.title) };
  const host = region();

  for (const existing of host.querySelectorAll("[data-console-toast]")) {
    if (
      existing.getAttribute("data-console-toast-level") === level &&
      existing.querySelector("[data-console-toast-message]")?.textContent === message
    ) {
      existing.remove();
    }
  }
  const template = document.querySelector("template[data-console-toast-template]");
  const toast = (template && fromTemplate(template, content)) || defaultToast(content);
  toast.setAttribute("data-console-toast", "");
  toast.setAttribute("data-console-toast-level", level);
  toast.setAttribute("role", spec.role);
  if (Number(options.duration) >= 0 && options.duration !== undefined) {
    toast.setAttribute("data-console-toast-timeout", String(options.duration));
  }
  const live = host.querySelectorAll("[data-console-toast]");
  for (let index = 0; index <= live.length - MAX_VISIBLE; index += 1) live[index].remove();
  host.append(toast);
  renderIcons(toast);
  arm(toast, spec.duration);
  return toast;
}

function armServerRendered() {
  for (const toast of document.querySelectorAll("[data-console-toast]:not([data-armed])")) {
    const level = levelOf(toast.getAttribute("data-console-toast-level") ?? "info");
    arm(toast, LEVELS[level].duration);
  }
}

export function initToasts() {
  armServerRendered();
  document.addEventListener("htmx:after:swap", armServerRendered);
  document.addEventListener("click", (event) => {
    const close = event.target instanceof Element ? event.target.closest("[data-console-toast-close]") : null;
    const toast = close?.closest("[data-console-toast]");
    if (toast instanceof HTMLElement) dismissToast(toast);
  });
}

// x-data="consoleToasts": optional hook so the host can expose `dismissAll` to markup.
export function toastsComponent() {
  return {
    dismissAll() {
      for (const toast of document.querySelectorAll("[data-console-toast]")) dismissToast(toast);
    },
  };
}
