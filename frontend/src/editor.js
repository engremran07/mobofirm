// Lazy loader for the rich-text editor. This tiny module is the only part shipped to
// pages that merely *contain* an editor field; the heavy bundle (Summernote + jQuery,
// `editor-core.js` + `editor-core.css`, same directory) is fetched only once an
// `textarea[data-mf-editor]` is actually visible, so collapsed reply forms, tabs and
// closed dialogs cost nothing and pages without an editor never load it.
const SELECTOR = "textarea[data-mf-editor]";
const base = import.meta.url.replace(/[^/]*$/, "");

let corePromise = null;
let styleLoaded = false;
const watching = new WeakSet();

function loadStyles() {
  if (styleLoaded) return;
  styleLoaded = true;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `${base}editor-core.css`;
  document.head.appendChild(link);
}

function loadCore() {
  if (corePromise) return corePromise;
  const mounted = new Promise((resolve) => {
    document.addEventListener("mf:editor-core", (event) => resolve(event.detail.mountEditors), { once: true });
  });
  loadStyles();
  corePromise = import(/* @vite-ignore */ `${base}editor-core.js`).then(() => mounted);
  return corePromise;
}

async function mount(targets) {
  const fresh = targets.filter((element) => !element.dataset.mfEditorReady);
  if (!fresh.length) return;
  try {
    const mountEditors = await loadCore();
    mountEditors(fresh);
  } catch (error) {
    corePromise = null;
    console.error("Rich text editor could not be loaded; using the plain textarea.", error);
  }
}

const observer =
  "IntersectionObserver" in window
    ? new IntersectionObserver(
        (entries) => {
          const visible = entries.filter((entry) => entry.isIntersecting).map((entry) => entry.target);
          visible.forEach((element) => observer.unobserve(element));
          if (visible.length) mount(visible);
        },
        { rootMargin: "300px 0px" },
      )
    : null;

function conditionMet(element) {
  const rule = element.dataset.mfEditorWhen;
  if (!rule) return true;
  const [name, value] = rule.split("=");
  const control = element.form && element.form.elements.namedItem(name);
  return Boolean(control) && control.value === value;
}

function scan() {
  document.querySelectorAll(SELECTOR).forEach((element) => {
    if (element.dataset.mfEditorReady || watching.has(element) || !conditionMet(element)) return;
    watching.add(element);
    if (observer) observer.observe(element);
    else mount([element]);
  });
}

let scheduled = false;
function schedule() {
  if (scheduled) return;
  scheduled = true;
  window.requestAnimationFrame(() => {
    scheduled = false;
    scan();
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", schedule);
else schedule();

// A controlling field (for example a page's content format) may switch the editor on.
document.addEventListener("change", schedule);

// Fields added later (HTMX swaps, admin "add another" rows, dialogs).
new MutationObserver((records) => {
  if (records.some((record) => record.addedNodes.length)) schedule();
}).observe(document.documentElement, { childList: true, subtree: true });
