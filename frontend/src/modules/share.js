// Native sharing for the share component (templates/includes/share.html). The Web
// Share API is an enhancement: without it the page keeps its copy-link button and the
// plain network links, and the native button stays hidden. No inline script or style.
import { showToast } from "./toasts.js";

function revealNativeButtons(root = document) {
  if (typeof navigator.share !== "function") return;
  for (const item of root.querySelectorAll("[data-share] [data-share-native-item]")) {
    item.hidden = false;
  }
}

async function shareNatively(widget) {
  const data = { title: widget.dataset.shareTitle, url: widget.dataset.shareUrl };
  if (widget.dataset.shareText) data.text = widget.dataset.shareText;
  try {
    await navigator.share(data);
  } catch (error) {
    // Closing the share sheet is a choice, not a failure.
    if (error instanceof DOMException && error.name === "AbortError") return;
    showToast({ level: "danger", message: "Sharing is not available here. Use Copy link instead." });
  }
}

export function initializeShare() {
  revealNativeButtons();
  document.addEventListener("htmx:afterSwap", (event) => {
    revealNativeButtons(event.target instanceof Element ? event.target : document);
  });
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-share-native]") : null;
    const widget = button?.closest("[data-share]");
    if (!widget) return;
    event.preventDefault();
    void shareNatively(widget);
  });
}
