// Visible HTMX failure feedback. Failures raise a danger toast (modules/toasts.js);
// the legacy #global-feedback region stays in the page as an empty live region.
import { showToast } from "./toasts.js";

function messageFor(status) {
  if (status === 403) return "You do not have permission to do that, or your session expired.";
  if (status === 404) return "That item could not be found.";
  if (status === 429) return "Too many requests. Please wait a moment and try again.";
  if (status >= 500) return "The server hit a problem. Please try again shortly.";
  return "That request could not be completed. Please try again.";
}

export function showFeedback(message, level = "danger") {
  return showToast({ message, level });
}

export function initializeFeedback() {
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
