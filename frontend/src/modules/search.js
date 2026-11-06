const TYPE_LABELS = {
  brand: "Brand",
  chipset: "Chipset",
  model: "Device",
  variant: "Variant",
  firmware: "Firmware",
  blog: "Article",
  tool: "Tool",
  guide: "Guide",
  page: "Page",
};

function isSafePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//");
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The autocomplete endpoint answers with JSON; build accessible DOM from it
// instead of inserting server markup, so no unsafe HTML reaches the page.
function renderSuggestions(payload, input, listboxId) {
  const fragment = document.createDocumentFragment();
  const results = Array.isArray(payload.results) ? payload.results : [];
  let index = 0;

  results.forEach((item) => {
    if (!isSafePath(item.url)) return;
    const option = element("a", "site-search-option");
    option.href = item.url;
    option.id = `${listboxId}-option-${index}`;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", "false");
    option.tabIndex = -1;
    option.append(
      element("span", "site-search-type", TYPE_LABELS[item.type] ?? "Result"),
      element("span", "site-search-name", String(item.name ?? "")),
    );
    if (item.subtitle) {
      option.append(element("span", "site-search-subtitle", String(item.subtitle)));
    }
    fragment.append(option);
    index += 1;
  });

  if (index === 0) {
    fragment.append(element("p", "site-search-empty", "No suggestions. Press Enter to search."));
  } else {
    const all = element("a", "site-search-option site-search-all", `See all results for “${payload.query}”`);
    all.href = `${input.form.action}?q=${encodeURIComponent(payload.query)}`;
    all.id = `${listboxId}-option-${index}`;
    all.setAttribute("role", "option");
    all.setAttribute("aria-selected", "false");
    all.tabIndex = -1;
    fragment.append(all);
  }

  const wrapper = document.createElement("div");
  wrapper.append(fragment);
  return wrapper.innerHTML;
}

function setExpanded(input, listbox, expanded) {
  input.setAttribute("aria-expanded", String(expanded));
  listbox.hidden = !expanded;
  if (!expanded) input.removeAttribute("aria-activedescendant");
}

function moveActive(input, listbox, step) {
  const options = Array.from(listbox.querySelectorAll('[role="option"]'));
  if (options.length === 0) return;
  const current = options.findIndex((option) => option.getAttribute("aria-selected") === "true");
  const next = (current + step + options.length) % options.length;
  options.forEach((option, position) => {
    option.setAttribute("aria-selected", String(position === next));
  });
  input.setAttribute("aria-activedescendant", options[next].id);
  options[next].scrollIntoView({ block: "nearest" });
}

export function initializeSiteSearch() {
  document.addEventListener("htmx:beforeRequest", (event) => {
    const input = event.detail.elt;
    if (input instanceof HTMLInputElement && input.dataset.siteSearch !== undefined) {
      if (input.value.trim().length < 2) event.preventDefault();
    }
  });

  document.addEventListener("htmx:beforeSwap", (event) => {
    const target = event.detail.target;
    if (!(target instanceof HTMLElement) || target.dataset.siteSearchResults === undefined) return;
    const input = document.querySelector(`[aria-controls="${target.id}"]`);
    if (!(input instanceof HTMLInputElement)) return;
    try {
      const payload = JSON.parse(event.detail.serverResponse);
      event.detail.serverResponse = renderSuggestions(payload, input, target.id);
    } catch (error) {
      event.detail.shouldSwap = false;
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    if (!(target instanceof HTMLElement) || target.dataset.siteSearchResults === undefined) return;
    const input = document.querySelector(`[aria-controls="${target.id}"]`);
    if (input instanceof HTMLInputElement) setExpanded(input, target, true);
  });

  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.dataset.siteSearch === undefined) return;
    const listbox = document.getElementById(input.getAttribute("aria-controls"));
    if (!listbox) return;

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (listbox.hidden) return;
      event.preventDefault();
      moveActive(input, listbox, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      const active = listbox.querySelector('[aria-selected="true"]');
      if (active instanceof HTMLAnchorElement) {
        event.preventDefault();
        window.location.assign(active.href);
      }
    } else if (event.key === "Escape" && !listbox.hidden) {
      event.stopPropagation();
      setExpanded(input, listbox, false);
    }
  });

  document.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.dataset.siteSearch === undefined) return;
    if (input.value.trim().length < 2) {
      const listbox = document.getElementById(input.getAttribute("aria-controls"));
      if (listbox) setExpanded(input, listbox, false);
    }
  });

  document.addEventListener("click", (event) => {
    document.querySelectorAll("[data-site-search-results]").forEach((listbox) => {
      const form = listbox.closest("form");
      if (form && !form.contains(event.target)) {
        const input = document.querySelector(`[aria-controls="${listbox.id}"]`);
        if (input instanceof HTMLInputElement) setExpanded(input, listbox, false);
      }
    });
  });
}
