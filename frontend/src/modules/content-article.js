// Behaviour of the content pages (journal, article, guide requests, comments): reading
// progress, table-of-contents scroll-spy, inline-reply focus and chip-bar scrolling.
// Everything is progressive enhancement; the pages work without it and no inline script
// or style is involved (strict CSP).

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// ---- Reading progress bar --------------------------------------------------------
function initializeReadingProgress() {
  const wrapper = document.querySelector("[data-read-progress]");
  const body = document.querySelector("[data-article-body]");
  if (!wrapper || !body) return;
  let queued = false;
  const update = () => {
    queued = false;
    const rect = body.getBoundingClientRect();
    const total = Math.max(rect.height, 1);
    const progress = clamp((window.innerHeight * 0.4 - rect.top) / total, 0, 1);
    wrapper.style.setProperty("--read-progress", progress.toFixed(4));
    wrapper.dataset.active = String(progress > 0 && progress < 1);
  };
  const schedule = () => {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(update);
  };
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule, { passive: true });
  update();
}

// ---- Table of contents scroll-spy -------------------------------------------------
function initializeTocSpy() {
  const links = [...document.querySelectorAll(".toc__item a[href^='#']")];
  if (links.length === 0 || typeof IntersectionObserver !== "function") return;
  const byId = new Map();
  for (const link of links) {
    const id = decodeURIComponent(link.getAttribute("href").slice(1));
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(link);
  }
  const headings = [...byId.keys()].map((id) => document.getElementById(id)).filter(Boolean);
  if (headings.length === 0) return;
  const setCurrent = (id) => {
    for (const link of links) link.removeAttribute("aria-current");
    for (const link of byId.get(id) ?? []) link.setAttribute("aria-current", "location");
  };
  const visible = new Set();
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      }
      const current = headings.find((heading) => visible.has(heading.id));
      if (current) setCurrent(current.id);
    },
    { rootMargin: "-90px 0px -60% 0px", threshold: 0 },
  );
  headings.forEach((heading) => observer.observe(heading));
}

// ---- Inline reply / edit panels: focus the editor when a panel opens -----------------
function initializeCommentPanels() {
  document.addEventListener(
    "toggle",
    (event) => {
      const panel = event.target;
      if (!(panel instanceof HTMLDetailsElement) || !panel.open) return;
      if (!panel.classList.contains("comment__panel")) return;
      window.setTimeout(() => {
        const field = panel.querySelector(".note-editable, textarea, select, input:not([type='hidden'])");
        if (field instanceof HTMLElement) field.focus({ preventScroll: false });
      }, 60);
    },
    true,
  );
}

// ---- Category chip bar: bring the active chip into view on narrow screens --------------
function revealActiveChips() {
  for (const bar of document.querySelectorAll(".chip-bar")) {
    const active = bar.querySelector(".chip__input:checked");
    const chip = active?.closest(".chip");
    if (!chip || bar.scrollWidth <= bar.clientWidth) continue;
    bar.scrollLeft = chip.offsetLeft - (bar.clientWidth - chip.offsetWidth) / 2;
  }
}

export function initializeContent() {
  initializeReadingProgress();
  initializeTocSpy();
  initializeCommentPanels();
  revealActiveChips();
}
