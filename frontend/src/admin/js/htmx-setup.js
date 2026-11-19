// htmx 4.0.2 wiring for the staff console. Strict CSP: no eval, no inline scripts, no
// inline handlers. Everything here is a global, document-level policy; pages stay plain
// HTML + htmx attributes.
//
// htmx 4 conventions chosen for the console (templates MUST follow them)
// ----------------------------------------------------------------------------
// * Transport is fetch(); events use the `htmx:phase:action` form (htmx:config:request,
//   htmx:before:request, htmx:before:response, htmx:after:request, htmx:response:error,
//   htmx:before:swap, htmx:after:swap, htmx:after:settle, htmx:finally:request). The
//   camelCase htmx-2 names do not exist; never listen for them.
// * Attribute inheritance is EXPLICIT: put `hx-target:inherited`, `hx-swap:inherited`,
//   `hx-boost:inherited`... on the parent. A bare `hx-target` on a container is NOT inherited
//   (implicitInheritance stays false). `:append` extends an inherited value.
// * Never use anything that evaluates strings (CSP): no `hx-on*`, no `hx-vals='js:...'`, no
//   `hx-confirm='js:...'`, no trigger filters in brackets (`click[ctrlKey]`), no <script> in
//   fragments (executable scripts and inline handlers are stripped from every swap here).
//   Use `hx-trigger="input changed delay:250ms"` style modifiers only.
// * `hx-confirm="text"` is routed to the app-owned confirm dialog (never window.confirm).
// * Boost: <body> sets `hx-boost:inherited="target:#console-main select:#console-main swap:outerHTML"` (HCON): a
//   click fetches the full page and swaps only #console-main. `shouldBoost` below limits it to plain console links
//   (no forms, files, popups, Bootstrap toggles or foreign origins). Boosted requests push history by default and
//   scroll to top. `HX-Boosted: true` and `HX-Request-Type: partial|full` tell the server what was asked.
//   Extensions (hx-head, hx-preload) are loaded from ./htmx-extensions.js.
// * History: no localStorage snapshot cache in htmx 4. Back/forward refetches the URL with
//   `HX-History-Restore-Request: true` and swaps the whole <body> (or [hx-history-elt]); the
//   server must answer that request with the FULL page, and vary on it.
// * Status handling: htmx 4 swaps every response except 204/304. noSwap below additionally
//   suppresses 401/403/404/405/408/410/413/429/5xx so error pages never land inside a card or
//   a dialog; those become an accessible toast. 400/409/422 DO swap: return the re-rendered
//   form with errors. Per-element overrides: `hx-status:422="target:#errors"`.
// * Redirects: a server redirect to the login page (expired session) becomes a full
//   navigation to login with `next` = the current page. `HX-Redirect`/`HX-Location`/
//   `HX-Refresh` are honoured by htmx itself.
// * Responses speak to the UI through `HX-Trigger`, fired as DOM events that bubble to
//   document (detail = the JSON value): `console-toast` (alias `toast`), `console-dialog-open`,
//   `console-dialog-close`, `console-refresh`. Regions refresh with
//   `hx-trigger="console-refresh from:body"` or `{"console-refresh": {"region": "#id"}}`.
// * CSRF: every non-GET request carries X-CSRFToken taken from
//   <meta name="csrf-token"> (the CSRF cookie is HttpOnly) or the closest csrfmiddlewaretoken.
// * Focus: after a swap, `[data-console-focus-after-swap]` inside (or being) the target is
//   focused; boosted navigation moves focus to #console-main and announces the new title.
// * View transitions: only for boosted navigation and `[data-console-transition]` regions,
//   only when supported and motion is not reduced.
import htmx from "htmx.org";

import "./htmx-extensions.js";

import { confirmDialog } from "./components/confirm.js";
import { showToast } from "./components/toasts.js";
import { UNSAVED_QUESTION, askToDiscard, dirtyForms } from "./components/unsaved.js";
import { renderIcons } from "./icons.js";
import { EVENTS, announce, emit, reducedMotion } from "./lib/util.js";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const NO_SWAP = [204, 304, 401, 403, 404, 405, 408, 410, 413, 429, "5xx"];
const BUSY_TTL_MS = 70000;

const MESSAGES = {
  400: "That didn't go through. Please check what you entered and try again.",
  403: "You don't have permission to do that. If you think you should, ask an administrator.",
  404: "We couldn't find that. It may have been moved or deleted.",
  405: "That action isn't available here.",
  408: "The server took too long to respond. Please try again.",
  409: "Someone else changed this at the same time. Reload the page to see the latest version.",
  410: "That item is no longer available.",
  413: "That file or request is too large.",
  429: "Too many requests. Please wait a moment and try again.",
  5: "Something went wrong on our side. Nothing was lost - please try again in a moment.",
};

const busy = new Map();
let redirectingToLogin = false;

function messageFor(status) {
  return MESSAGES[status] ?? (status >= 500 ? MESSAGES[5] : MESSAGES[400]);
}

function suppressesSwap(status) {
  const patterns = htmx.config.noSwap.map(String);
  const text = String(status);
  return [text, `${text.slice(0, 2)}x`, `${text[0]}xx`].some((pattern) => patterns.includes(pattern));
}

function csrfToken(source) {
  const meta = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  if (meta) return meta;
  const field =
    source?.closest?.("form")?.querySelector('input[name="csrfmiddlewaretoken"]') ??
    document.querySelector('input[name="csrfmiddlewaretoken"]');
  return field?.value ?? "";
}

function loginUrl() {
  return document.querySelector('meta[name="console-login-url"]')?.getAttribute("content") ?? "";
}

function isLoginUrl(url) {
  const login = loginUrl();
  try {
    const target = new URL(url, window.location.href);
    if (login) return target.pathname === new URL(login, window.location.href).pathname;
    return /\/login\/?$/.test(target.pathname);
  } catch {
    return false;
  }
}

function goToLogin(responseUrl) {
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  const target = new URL(responseUrl || loginUrl() || "/", window.location.href);
  target.searchParams.set("next", window.location.pathname + window.location.search);
  window.location.assign(target.toString());
}

// Executable scripts and inline event handlers never enter the DOM through a swap.
function sanitizeFragment(fragment) {
  if (!fragment?.querySelectorAll) return;
  for (const script of fragment.querySelectorAll("script")) {
    const type = (script.getAttribute("type") ?? "").toLowerCase();
    if (type === "" || type.includes("javascript") || type === "module") script.remove();
  }
  for (const element of fragment.querySelectorAll("*")) {
    for (const { name } of [...element.attributes]) {
      if (/^(on|hx-on|data-hx-on)/i.test(name)) element.removeAttribute(name);
    }
  }
}

function setBusy() {
  const now = Date.now();
  for (const [ctx, startedAt] of busy) {
    if (now - startedAt > BUSY_TTL_MS || ctx.status === "swapped" || String(ctx.status).startsWith("error")) {
      busy.delete(ctx);
    }
  }
  const active = busy.size > 0;
  document.documentElement.toggleAttribute("data-console-busy", active);
  const bar = document.querySelector("[data-console-progress]");
  if (bar) {
    bar.toggleAttribute("data-active", active);
    bar.setAttribute("aria-hidden", "true");
  }
}

function serverSentToast(ctx) {
  return /toast/.test(ctx?.hx?.trigger ?? "");
}

function isBoosted(ctx) {
  return ctx?.request?.headers?.["HX-Boosted"] === "true";
}

function insideDialog(element) {
  return element instanceof Element && Boolean(element.closest("#console-dialog"));
}

const VERB_SELECTOR = ["get", "post", "put", "patch", "delete", "action"]
  .flatMap((verb) => [`[hx-${verb}]`, `[data-hx-${verb}]`])
  .join(",");
const NO_BOOST_LINK = "[download], [data-bs-toggle], [data-console-no-boost], [data-console-related-link], [role='button']";
const FILE_PATH = /\.[a-z0-9]{2,5}$/i;

// `hx-boost:inherited` sits on <body>; this decides which elements really take part: only plain links
// into the console. Forms keep their native POST/redirect (CSRF, uploads, MFA, logout), and links to
// files, popups, the public site, dialogs or Bootstrap toggles keep their normal behaviour.
function shouldBoost(element) {
  if (element.matches(VERB_SELECTOR)) return true; // not a boost candidate: leave htmx alone
  if (element instanceof HTMLFormElement) return element.hasAttribute("data-console-boost");
  if (!(element instanceof HTMLAnchorElement)) return true;
  if (element.matches(NO_BOOST_LINK) || element.closest(".modal, [data-console-dialog]")) return false;
  const prefix = document.querySelector('meta[name="console-url-prefix"]')?.getAttribute("content") ?? "/admin/";
  let url;
  try {
    url = new URL(element.href, window.location.href);
  } catch {
    return false;
  }
  return (
    url.origin === window.location.origin &&
    url.pathname.startsWith(prefix) &&
    !FILE_PATH.test(url.pathname) &&
    !url.searchParams.has("_popup")
  );
}

function onBeforeInit(event) {
  if (event.target instanceof Element && !shouldBoost(event.target)) event.preventDefault();
}

function onConfigRequest(event) {
  const { ctx } = event.detail;
  const method = String(ctx.request.method ?? "GET").toUpperCase();
  if (!SAFE_METHODS.has(method)) {
    const token = csrfToken(ctx.sourceElement);
    if (token) ctx.request.headers["X-CSRFToken"] = token;
  }
  const wantsTransition =
    "startViewTransition" in document &&
    !reducedMotion() &&
    !insideDialog(ctx.target) &&
    (isBoosted(ctx) || Boolean(ctx.sourceElement?.closest?.("[data-console-transition]")));
  ctx.transition = wantsTransition;
}

function onBeforeRequest(event) {
  busy.set(event.detail.ctx, Date.now());
  setBusy();
}

function onBeforeResponse(event) {
  const { ctx } = event.detail;
  const raw = ctx.response?.raw;
  if (!raw) return;
  const requestedLogin = isLoginUrl(ctx.request.action);
  if ((raw.redirected && isLoginUrl(raw.url) && !requestedLogin) || ctx.response.status === 401) {
    event.preventDefault();
    goToLogin(raw.redirected ? raw.url : loginUrl());
  }
}

function onResponseError(event) {
  const { ctx } = event.detail;
  const status = ctx.response?.status ?? 0;
  if (serverSentToast(ctx)) return;
  const hasMarkup = /</.test(ctx.text ?? "");
  if (!suppressesSwap(status) && hasMarkup) return; // a re-rendered form swaps in; nothing to toast
  showToast({ level: "danger", title: "That didn't work", message: messageFor(status) });
}

function onError(event) {
  const { ctx, error } = event.detail;
  if (ctx?.request?.signal?.reason === "timeout") {
    showToast({ level: "warning", title: "Taking too long", message: messageFor(408) });
  } else if (error instanceof TypeError) {
    showToast({
      level: "danger",
      title: "You appear to be offline",
      message: "We couldn't reach the server. Check your connection and try again.",
    });
  } else {
    showToast({ level: "danger", title: "That didn't work", message: messageFor(500) });
  }
}

function onBeforeSwap(event) {
  const { ctx, tasks } = event.detail;
  if ((ctx.response?.status ?? 0) >= 400 && !/</.test(ctx.text ?? "")) {
    event.preventDefault();
    return;
  }
  for (const task of tasks ?? []) sanitizeFragment(task.fragment);
}

function applyFocus(ctx) {
  const target = ctx.target instanceof Element && ctx.target.isConnected ? ctx.target : document;
  const marked =
    (target instanceof Element && target.matches("[data-console-focus-after-swap]") ? target : null) ??
    target.querySelector("[data-console-focus-after-swap]");
  if (marked instanceof HTMLElement) {
    if (!marked.hasAttribute("tabindex") && !marked.matches("a, button, input, select, textarea")) {
      marked.setAttribute("tabindex", "-1");
    }
    marked.focus({ preventScroll: true });
    return true;
  }
  return false;
}

function onAfterSwap(event) {
  const { ctx } = event.detail;
  renderIcons();
  if (isBoosted(ctx)) {
    const main = document.getElementById("console-main");
    if (main && !applyFocus(ctx)) {
      if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
      main.focus({ preventScroll: true });
    }
    announce(document.title);
    emit(EVENTS.navigated, { url: window.location.href });
  } else {
    applyFocus(ctx);
  }
}

function onFinally() {
  setBusy();
}

// HX-Trigger events -------------------------------------------------------------
function onToastEvent(event) {
  const detail = event.detail;
  if (typeof detail === "string") showToast({ message: detail });
  else showToast(detail?.value && !detail.message ? { message: String(detail.value) } : (detail ?? {}));
}

function onRefresh(event) {
  const region = event.detail?.region;
  if (typeof region !== "string") return;
  for (const element of document.querySelectorAll(region)) htmx.trigger(element, EVENTS.refresh);
}

// Replaces htmx's native window.confirm: any hx-confirm text opens the app dialog.
function onConfirm(event) {
  const { ctx, issueRequest, dropRequest } = event.detail;
  const question = ctx.confirm;
  if (!question || /^js:/.test(question)) {
    if (question) dropRequest();
    return;
  }
  event.preventDefault();
  const source = ctx.sourceElement instanceof Element ? ctx.sourceElement : null;
  const guarded = question === UNSAVED_QUESTION;
  const ask = guarded
    ? askToDiscard(dirtyForms().filter((form) => ctx.target?.contains?.(form)))
    : confirmDialog(confirmCopy(source, question));
  ask.then((confirmed) => (confirmed ? issueRequest() : dropRequest()));
}

function confirmCopy(source, question) {
  const data = source?.closest("[data-console-confirm-title],[data-console-confirm-danger]")?.dataset ?? {};
  return {
    title: data.consoleConfirmTitle ?? "Please confirm",
    message: question,
    confirmLabel: data.consoleConfirmLabel,
    cancelLabel: data.consoleConfirmCancel,
    danger: data.consoleConfirmDanger !== undefined && data.consoleConfirmDanger !== "false",
    opener: source,
  };
}

export function setupHtmx() {
  const config = htmx.config;
  config.implicitInheritance = false;
  config.noSwap = NO_SWAP;
  config.defaultTimeout = 45000;
  config.history = true;
  config.transitions = false;
  config.defaultFocusScroll = false;

  const listeners = {
    "htmx:before:init": onBeforeInit,
    "htmx:config:request": onConfigRequest,
    "htmx:before:request": onBeforeRequest,
    "htmx:before:response": onBeforeResponse,
    "htmx:response:error": onResponseError,
    "htmx:error": onError,
    "htmx:before:swap": onBeforeSwap,
    "htmx:after:swap": onAfterSwap,
    "htmx:finally:request": onFinally,
    "htmx:confirm": onConfirm,
    [EVENTS.toast]: onToastEvent,
    toast: onToastEvent,
    [EVENTS.refresh]: onRefresh,
  };
  for (const [name, handler] of Object.entries(listeners)) document.addEventListener(name, handler);

  // A dropped/aborted request never reaches `finally` on a detached element in every
  // path; the TTL sweep keeps the progress indicator honest.
  window.setInterval(setBusy, 5000);

  document.addEventListener("htmx:before:history:restore", () => announce("Loading previous page"));
  return htmx;
}

export { htmx };
