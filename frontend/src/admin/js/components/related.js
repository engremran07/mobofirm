// Related-object popups (add / change / delete / view next to a relation field), rendered
// as console dialogs instead of browser windows.
//   wrapper  .related-widget-wrapper[data-console-related]   (holds the <select> or raw-id input)
//   links    [data-console-related-link="add|change|delete|view"] with `href` (add) or
//            `data-href-template` containing `__fk__` (replaced by the select's value)
// The dialog form posts via htmx; Django answers with `[data-console-popup-response]`
// whose `data-popup-response` is the admin's popup JSON ({action, value, obj, new_value}).
// handlePopupResponse() applies it to the owning widget; dialog.js then closes the dialog.
import htmx from "htmx.org";

import { showToast } from "./toasts.js";
import { EVENTS, emit } from "../lib/util.js";

let activeWrapper = null;

function targetValue(wrapper) {
  const field = wrapper.querySelector("select, input:not([type=hidden]), input[type=hidden]");
  return field instanceof HTMLSelectElement || field instanceof HTMLInputElement ? field.value : "";
}

function popupUrl(link, wrapper) {
  let url = link.getAttribute("href");
  const template = link.dataset.hrefTemplate;
  if (template) {
    const value = targetValue(wrapper);
    if (!value) return null;
    url = template.replace("__fk__", encodeURIComponent(value));
  }
  if (!url) return null;
  const resolved = new URL(url, window.location.href);
  if (!resolved.searchParams.has("_popup")) resolved.searchParams.set("_popup", "1");
  return resolved.pathname + resolved.search;
}

function onClick(event) {
  const link = event.target instanceof Element ? event.target.closest("[data-console-related-link]") : null;
  if (!(link instanceof HTMLElement)) return;
  const wrapper = link.closest("[data-console-related]");
  if (!wrapper) return;
  event.preventDefault();
  const url = popupUrl(link, wrapper);
  if (!url) {
    showToast({ level: "info", message: "Choose an item first, then try again." });
    return;
  }
  activeWrapper = wrapper;
  htmx.ajax("get", url, { target: "#console-dialog", swap: "innerHTML", source: link });
}

function setOptions(select, data) {
  const { action, value, obj } = data;
  const options = [...select.options];
  if (action === "add") {
    const option = new Option(obj, value, true, true);
    select.append(option);
  } else if (action === "change") {
    const option = options.find((candidate) => candidate.value === String(data.value));
    if (option) {
      option.value = String(data.new_value ?? value);
      option.textContent = obj;
    }
  } else if (action === "delete") {
    options.find((candidate) => candidate.value === String(value))?.remove();
  }
}

export function handlePopupResponse(element) {
  let data;
  try {
    data = JSON.parse(element.getAttribute("data-popup-response") ?? "");
  } catch {
    return;
  }
  const wrapper = activeWrapper;
  activeWrapper = null;
  const field = wrapper?.querySelector("select");
  if (field instanceof HTMLSelectElement) {
    setOptions(field, data);
    field.dispatchEvent(new Event("change", { bubbles: true }));
    field.dispatchEvent(new CustomEvent(EVENTS.comboboxSync, { bubbles: true }));
  } else {
    const input = wrapper?.querySelector("input");
    if (input instanceof HTMLInputElement && data.action !== "delete") input.value = String(data.new_value ?? data.value);
    if (input instanceof HTMLInputElement && data.action === "delete") input.value = "";
  }
  emit(EVENTS.popupResponse, data);
  const verb = { add: "added", change: "updated", delete: "deleted" }[data.action];
  if (verb && data.obj !== undefined) showToast({ level: "success", message: `“${data.obj}” ${verb}.` });
  else if (verb) showToast({ level: "success", message: `Item ${verb}.` });
}

export function initRelated() {
  document.addEventListener("click", onClick);
}
