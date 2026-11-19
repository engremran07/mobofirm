// Autocomplete combobox (templates/console/widgets/autocomplete.html), the replacement for select2.
//
// Hooks: root [data-console-combobox] with data-url, data-app-label, data-model-name,
// data-field-name, optional data-multiple / data-clearable; the real <select
// data-console-combobox-select> stays the submitted value; [data-console-combobox-input] is
// the search box; ul.c-combobox__list[role=listbox] receives the options; .c-combobox__tokens
// shows the selection as chips. Options come from Django's admin autocomplete JSON endpoint:
//   GET <url>?term=&app_label=&model_name=&field_name=&page=  ->  {results:[{id,text}], pagination:{more}}
// Keyboard: ArrowDown/ArrowUp/Home/End move, Enter picks, Escape closes, Backspace on an
// empty search removes the last chip (multiple). Text is only ever written with textContent.
import { iconPlaceholder, renderIcons } from "../icons.js";
import { EVENTS, announce, debounce, ensureId } from "../lib/util.js";

const SEARCH_DELAY_MS = 200;

export function attachCombobox(root) {
  const select = root.querySelector("[data-console-combobox-select]");
  const input = root.querySelector("[data-console-combobox-input]");
  const list = root.querySelector(".c-combobox__list");
  const tokens = root.querySelector(".c-combobox__tokens");
  if (!(select instanceof HTMLSelectElement) || !(input instanceof HTMLInputElement) || !list || !tokens) {
    return { destroy() {} };
  }
  const controller = new AbortController();
  const { signal } = controller;
  const multiple = root.hasAttribute("data-multiple");
  const clearable = root.hasAttribute("data-clearable") || multiple;
  const listId = ensureId(list, "console-combobox-list");
  let request = null;
  let page = 1;
  let more = false;
  let term = "";
  let active = -1;

  input.setAttribute("aria-controls", listId);
  input.disabled = select.disabled;
  select.hidden = true;
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  if (select.id) {
    ensureId(input, `${select.id}-search`);
    for (const label of document.querySelectorAll(`label[for="${CSS.escape(select.id)}"]`)) label.htmlFor = input.id;
  }

  const options = () => [...list.querySelectorAll('[role="option"][data-value]')];
  const selectedValues = () => [...select.selectedOptions].map((option) => option.value).filter(Boolean);

  function setActive(index, scroll = true) {
    const items = options();
    for (const item of items) {
      item.classList.remove("is-active");
      item.removeAttribute("data-active");
    }
    active = items.length === 0 ? -1 : Math.max(0, Math.min(index, items.length - 1));
    const item = items[active];
    if (!item) {
      input.removeAttribute("aria-activedescendant");
      return;
    }
    item.classList.add("is-active");
    item.setAttribute("data-active", "");
    input.setAttribute("aria-activedescendant", ensureId(item, "console-combobox-option"));
    if (scroll) item.scrollIntoView({ block: "nearest" });
  }

  function open() {
    list.hidden = false;
    root.setAttribute("data-open", "");
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    list.hidden = true;
    root.removeAttribute("data-open");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
    request?.abort();
  }

  function renderTokens() {
    const chips = [];
    for (const option of select.selectedOptions) {
      if (!option.value) continue;
      const chip = document.createElement("span");
      chip.className = "c-chip";
      chip.dataset.value = option.value;
      chip.append(document.createTextNode(option.textContent ?? ""));
      if (clearable && !select.disabled) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "c-chip__remove";
        remove.dataset.consoleComboboxRemove = option.value;
        remove.setAttribute("aria-label", `Remove ${option.textContent ?? ""}`);
        remove.append(iconPlaceholder("x"));
        chip.append(remove);
      }
      chips.push(chip);
    }
    tokens.replaceChildren(...chips);
    renderIcons(tokens);
  }

  function changed() {
    renderTokens();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function choose(value, text) {
    const existing = [...select.options].find((option) => option.value === value);
    if (multiple) {
      if (existing?.selected) {
        existing.selected = false;
        announce(`${text} removed`);
      } else if (existing) {
        existing.selected = true;
        announce(`${text} added`);
      } else {
        select.append(new Option(text, value, true, true));
        announce(`${text} added`);
      }
      changed();
      input.value = "";
      return;
    }
    for (const option of [...select.options]) {
      if (option.value !== value) option.remove();
    }
    if (existing) existing.selected = true;
    else select.append(new Option(text, value, true, true));
    select.value = value;
    changed();
    input.value = "";
    close();
    announce(`${text} selected`);
  }

  function clearSingle(value) {
    for (const option of [...select.options]) {
      if (option.value === value) {
        if (multiple) option.selected = false;
        else option.remove();
      }
    }
    if (!multiple && ![...select.options].some((option) => option.value === "")) {
      select.prepend(new Option("---------", "", true, true));
    }
    select.value = multiple ? select.value : "";
    changed();
  }

  function renderOption(item) {
    const li = document.createElement("li");
    li.className = "c-combobox__option";
    li.setAttribute("role", "option");
    li.dataset.value = String(item.id);
    li.dataset.text = item.text;
    const picked = selectedValues().includes(String(item.id));
    li.setAttribute("aria-selected", String(picked));
    li.toggleAttribute("data-selected", picked);
    li.textContent = item.text;
    return li;
  }

  function status(message) {
    const li = document.createElement("li");
    li.className = "c-combobox__option";
    li.setAttribute("aria-disabled", "true");
    li.setAttribute("data-disabled", "");
    li.setAttribute("role", "presentation");
    li.textContent = message;
    return li;
  }

  async function load(append) {
    request?.abort();
    request = new AbortController();
    const url = new URL(root.dataset.url ?? "", window.location.href);
    url.searchParams.set("term", term);
    url.searchParams.set("app_label", root.dataset.appLabel ?? "");
    url.searchParams.set("model_name", root.dataset.modelName ?? "");
    url.searchParams.set("field_name", root.dataset.fieldName ?? "");
    url.searchParams.set("page", String(page));
    if (!append) list.replaceChildren(status("Searching…"));
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        signal: request.signal,
      });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      more = Boolean(data.pagination?.more);
      list.querySelector("[data-console-combobox-more]")?.remove();
      if (!append) list.replaceChildren();
      for (const item of data.results ?? []) list.append(renderOption(item));
      if (options().length === 0) list.replaceChildren(status("No matches. Try a different word."));
      if (more) {
        const next = status("Load more results…");
        next.setAttribute("role", "option");
        next.removeAttribute("aria-disabled");
        next.dataset.consoleComboboxMore = "";
        next.dataset.value = "";
        list.append(next);
      }
      if (!append) setActive(0, false);
    } catch (error) {
      if (error?.name === "AbortError") return;
      list.replaceChildren(status("We couldn't load the options. Please try again."));
    }
  }

  const search = debounce(() => {
    term = input.value.trim();
    page = 1;
    open();
    load(false);
  }, SEARCH_DELAY_MS);

  function loadMore() {
    page += 1;
    load(true);
  }

  function pick(item) {
    if (!item) return;
    if (item.hasAttribute("data-console-combobox-more")) {
      loadMore();
      return;
    }
    choose(item.dataset.value ?? "", item.dataset.text ?? item.textContent ?? "");
    if (multiple) setActive(active);
  }

  input.addEventListener("input", search, { signal });
  input.addEventListener(
    "focus",
    () => {
      if (list.hidden && !select.disabled) {
        term = input.value.trim();
        page = 1;
        open();
        load(false);
      }
    },
    { signal },
  );
  input.addEventListener(
    "keydown",
    (event) => {
      const items = options();
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (list.hidden) {
          open();
          load(false);
          return;
        }
        setActive(active + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Home" && !list.hidden) {
        event.preventDefault();
        setActive(0);
      } else if (event.key === "End" && !list.hidden) {
        event.preventDefault();
        setActive(items.length - 1);
      } else if (event.key === "Enter" && !list.hidden && items[active]) {
        event.preventDefault();
        pick(items[active]);
      } else if (event.key === "Escape" && !list.hidden) {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Backspace" && multiple && input.value === "") {
        const last = selectedValues().at(-1);
        if (last) clearSingle(last);
      }
    },
    { signal },
  );

  list.addEventListener(
    "mousedown",
    (event) => {
      // Keep focus in the search box while an option is clicked.
      event.preventDefault();
    },
    { signal },
  );
  list.addEventListener(
    "click",
    (event) => {
      const item = event.target instanceof Element ? event.target.closest('[role="option"]') : null;
      if (item && list.contains(item)) pick(item);
    },
    { signal },
  );
  list.addEventListener(
    "scroll",
    () => {
      if (more && list.scrollTop + list.clientHeight >= list.scrollHeight - 24) {
        more = false;
        loadMore();
      }
    },
    { signal },
  );

  tokens.addEventListener(
    "click",
    (event) => {
      const remove = event.target instanceof Element ? event.target.closest("[data-console-combobox-remove]") : null;
      if (!(remove instanceof HTMLElement)) return;
      clearSingle(remove.dataset.consoleComboboxRemove ?? "");
      input.focus();
    },
    { signal },
  );

  document.addEventListener(
    "pointerdown",
    (event) => {
      if (!list.hidden && event.target instanceof Node && !root.contains(event.target)) close();
    },
    { signal },
  );
  select.addEventListener(EVENTS.comboboxSync, renderTokens, { signal });
  root.addEventListener("focusout", (event) => {
    if (!(event.relatedTarget instanceof Node) || !root.contains(event.relatedTarget)) close();
  }, { signal });

  input.setAttribute("aria-expanded", "false");
  renderTokens();
  return {
    destroy() {
      search.cancel();
      request?.abort();
      controller.abort();
    },
  };
}
