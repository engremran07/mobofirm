const container = () => document.getElementById("global-feedback");

function messageFor(status) {
  if (status === 403) return "You do not have permission to do that, or your session expired.";
  if (status === 404) return "That item could not be found.";
  if (status === 429) return "Too many requests. Please wait a moment and try again.";
  if (status >= 500) return "The server hit a problem. Please try again shortly.";
  return "That request could not be completed. Please try again.";
}

export function showFeedback(message, level = "danger") {
  const region = container();
  if (!region) return;
  const alert = document.createElement("div");
  alert.className = `alert alert-${level} alert-dismissible mb-0 mt-3`;
  alert.setAttribute("role", level === "danger" ? "alert" : "status");
  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "btn-close";
  close.setAttribute("aria-label", "Dismiss message");
  close.addEventListener("click", () => alert.remove());
  alert.append(text, close);
  region.replaceChildren(alert);
}

export function initializeFeedback() {
  document.addEventListener("htmx:beforeRequest", () => {
    container()?.replaceChildren();
  });
  document.addEventListener("htmx:responseError", (event) => {
    showFeedback(messageFor(event.detail.xhr.status));
  });
  document.addEventListener("htmx:sendError", () => {
    showFeedback("The network is unavailable. Check your connection and try again.");
  });
  document.addEventListener("htmx:timeout", () => {
    showFeedback("The request timed out. Please try again.");
  });
}
