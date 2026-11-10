// Accessible combobox (ARIA 1.2 editable combobox with a listbox popup) for Alpine's CSP
// build. The server renders the options (`GET /search/suggest/`, swapped in by HTMX on
// every keystroke); this component owns only local state: open/closed, the active
// option and, in "pick" mode, the chosen value. See docs/05-frontend/components.md.
//
// Events (bubbling CustomEvents on the component root):
//   combobox:select  detail { id, name, value, label, data, source }
//   combobox:clear   detail { id, name }
// Listen for `combobox:set` on the root to set a value from outside:
//   detail { value, label, onlyIfEmpty }.
// Navigation comboboxes also offer recent searches (this browser only, switchable off with
// data-recent="false" or from the list itself) and the "/" shortcut (modules/search.js).

import {
  clearRecentSearches,
  initializeSearchShortcuts,
  readRecentSearches,
  recentSearchesEnabled,
  rememberSearch,
  setRecentSearchesEnabled,
} from "./search.js";

const normalize = (text) => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

function emit(root, type, detail) {
  root.dispatchEvent(new CustomEvent(type, { bubbles: true, detail }));
}

function recentOption(id, label, attributes) {
  const option = document.createElement("div");
  option.id = id;
  option.className = "combobox-option combobox-recent-option";
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", "false");
  Object.entries(attributes).forEach(([name, value]) => option.setAttribute(name, value));
  const text = document.createElement("span");
  text.className = "combobox-text";
  const title = document.createElement("span");
  title.className = "combobox-title";
  title.textContent = label;
  text.append(title);
  option.append(text);
  return option;
}

function buildRecentPanel(prefix, searches) {
  const group = document.createElement("div");
  group.className = "combobox-group combobox-group--recent";
  group.setAttribute("role", "group");
  group.setAttribute("aria-labelledby", `${prefix}-group-recent`);
  const heading = document.createElement("div");
  heading.id = `${prefix}-group-recent`;
  heading.className = "combobox-group__label";
  heading.setAttribute("role", "presentation");
  heading.textContent = "Recent searches";
  group.append(heading);
  searches.forEach((query, index) => {
    group.append(recentOption(`${prefix}-option-recent-${index}`, query, { "data-recent-value": query }));
  });
  const clear = recentOption(`${prefix}-option-recent-clear`, "Clear recent searches", { "data-action": "clear-recent" });
  const disable = recentOption(`${prefix}-option-recent-off`, "Turn off recent searches", { "data-action": "disable-recent" });
  clear.classList.add("combobox-recent-action");
  disable.classList.add("combobox-recent-action");
  group.append(clear, disable);
  return group;
}

export function registerCombobox(Alpine) {
  initializeSearchShortcuts();
  Alpine.data("combobox", () => ({
    mode: "pick",
    minChars: 0,
    dependsOn: "",
    dependsParam: "",
    open: false,
    focused: false,
    loading: false,
    activeIndex: -1,
    text: "",
    value: "",
    selectedLabel: "",
    detected: false,
    dirty: false,
    forceRequest: false,
    recentShown: false,
    recentAllowed: false,
    status: "",

    init() {
      const settings = this.$root.dataset;
      this.mode = settings.mode === "navigate" ? "navigate" : "pick";
      this.minChars = Number.parseInt(settings.minChars ?? "0", 10) || 0;
      this.dependsOn = settings.dependsOn ?? "";
      this.dependsParam = settings.dependsParam ?? "";
      this.detected = settings.detected === "true";
      this.recentAllowed = this.mode === "navigate" && settings.recent !== "false";
      this.text = this.$refs.input.value;
      this.value = this.valueField ? this.valueField.value : "";
      this.selectedLabel = this.value ? this.text : "";
      this.syncDisabled();
      const form = this.$refs.input.form;
      if (form && this.recentAllowed) {
        form.addEventListener("submit", () => rememberSearch(this.$refs.input.value));
      }
    },

    get valueField() {
      return document.getElementById(`${this.$refs.input.id}-value`);
    },
    get closed() {
      return !this.open;
    },
    get expandedAttr() {
      return this.open ? "true" : "false";
    },
    get isEmpty() {
      return this.text === "";
    },
    get activeId() {
      const option = this.options()[this.activeIndex];
      return this.open && option ? option.id : null;
    },
    options() {
      return Array.from(
        this.$refs.list.querySelectorAll('[role="option"]:not([aria-disabled="true"])'),
      );
    },
    parentValue() {
      if (!this.dependsOn) return "";
      const parent = document.getElementById(`${this.dependsOn}-value`);
      return parent ? parent.value : "";
    },
    syncDisabled() {
      if (!this.dependsOn) return;
      this.$refs.input.disabled = this.parentValue() === "";
    },

    // ---- typing and requests -------------------------------------------------
    onInput() {
      this.text = this.$refs.input.value;
      this.dirty = true;
      this.detected = false;
      if (this.mode === "pick" && this.value !== "") this.setValue("", "", false);
      this.setActive(-1);
      if (this.text.trim() === "" && this.showRecent()) return;
      if (this.text.trim().length < this.minChars) this.close();
    },
    onFocus() {
      this.focused = true;
      // A chosen value is replaced, not extended: select it so typing overwrites it.
      if (this.mode === "pick" && this.value !== "") this.$refs.input.select();
      if (this.text.trim() === "") this.showRecent();
    },
    // Recent searches fill the list while the field is empty (navigation mode only).
    showRecent() {
      if (!this.recentAllowed || !this.focused || !recentSearchesEnabled()) return false;
      const searches = readRecentSearches();
      if (searches.length === 0) return false;
      this.$refs.list.replaceChildren(buildRecentPanel(this.$refs.input.id, searches));
      this.recentShown = true;
      this.activeIndex = -1;
      this.status = `${searches.length} recent search${searches.length === 1 ? "" : "es"}. Use the up and down arrow keys to review.`;
      this.open = true;
      return true;
    },
    onBeforeRequest(event) {
      if (event.target !== this.$refs.input) return;
      const forced = this.forceRequest;
      this.forceRequest = false;
      const hasSelection = this.mode === "pick" && this.value !== "" && this.text === this.selectedLabel;
      const tooShort = this.text.trim().length < this.minChars;
      const blocked = this.dependsOn && this.parentValue() === "";
      if (tooShort || blocked || (hasSelection && !forced)) {
        event.preventDefault();
        const keepRecent = this.recentShown && this.text.trim() === "";
        if ((tooShort || blocked) && !keepRecent) this.close();
        return;
      }
      this.loading = true;
    },
    onConfigRequest(event) {
      if (event.target !== this.$refs.input || !this.dependsParam) return;
      event.detail.parameters[`filter.${this.dependsParam}`] = this.parentValue();
    },
    onAfterRequest(event) {
      if (event.target === this.$refs.input) this.loading = false;
    },
    onResults(event) {
      if (event.detail.target !== this.$refs.list) return;
      this.recentShown = false;
      const count = this.options().length;
      this.activeIndex = -1;
      this.status =
        count === 0
          ? "No results."
          : `${count} result${count === 1 ? "" : "s"} available. Use the up and down arrow keys to review.`;
      this.open = this.focused;
    },
    requestSuggestions() {
      this.forceRequest = true;
      window.htmx.trigger(this.$refs.input, "mf:suggest");
    },

    // ---- keyboard -----------------------------------------------------------
    onKeydown(event) {
      const options = this.options();
      switch (event.key) {
        case "ArrowDown":
        case "ArrowUp": {
          event.preventDefault();
          if (!this.open) {
            this.open = options.length > 0;
            if (!this.open) this.requestSuggestions();
            return;
          }
          if (options.length === 0) return;
          const step = event.key === "ArrowDown" ? 1 : -1;
          const next = this.activeIndex === -1 && step === -1 ? options.length - 1 : this.activeIndex + step;
          this.setActive((next + options.length) % options.length);
          break;
        }
        case "Home":
        case "End":
          if (this.open && this.activeIndex !== -1 && options.length > 0) {
            event.preventDefault();
            this.setActive(event.key === "Home" ? 0 : options.length - 1);
          }
          break;
        case "Enter": {
          if (!this.open) {
            if (this.mode === "pick") event.preventDefault();
            return;
          }
          const option = options[this.activeIndex] ?? (this.mode === "pick" ? options[0] : undefined);
          if (option) {
            event.preventDefault();
            this.choose(option);
          } else if (this.mode === "pick") {
            event.preventDefault();
          }
          break;
        }
        case "Escape":
          if (this.open) {
            event.preventDefault();
            event.stopPropagation();
            this.close();
          } else if (this.text !== "") {
            event.preventDefault();
            this.clear();
          }
          break;
        case "Tab":
          this.close();
          break;
        default:
      }
    },
    setActive(index) {
      const options = this.options();
      this.activeIndex = index;
      options.forEach((option, position) => {
        option.setAttribute("aria-selected", position === index ? "true" : "false");
      });
      const active = options[index];
      if (active) active.scrollIntoView({ block: "nearest" });
    },
    close() {
      this.open = false;
      this.activeIndex = -1;
    },

    // ---- choosing -------------------------------------------------------------
    onListClick(event) {
      const option = event.target.closest('[role="option"]');
      if (!option || option.getAttribute("aria-disabled") === "true") return;
      if (this.mode === "pick" || option.dataset.action || option.dataset.recentValue) {
        event.preventDefault();
        this.choose(option);
      } else if (this.recentAllowed) {
        rememberSearch(option.dataset.recentQuery ?? this.text);
      }
    },
    choose(option) {
      if (option.dataset.action === "clear-recent") {
        clearRecentSearches();
        this.recentShown = false;
        this.close();
        return;
      }
      if (option.dataset.action === "disable-recent") {
        setRecentSearchesEnabled(false);
        this.recentShown = false;
        this.close();
        return;
      }
      if (option.dataset.recentValue !== undefined) {
        this.text = option.dataset.recentValue;
        this.$refs.input.value = this.text;
        this.recentShown = false;
        this.close();
        const form = this.$refs.input.form;
        if (form) form.requestSubmit();
        return;
      }
      if (this.mode === "navigate") {
        if (option instanceof HTMLAnchorElement) {
          if (this.recentAllowed) rememberSearch(option.dataset.recentQuery ?? this.text);
          window.location.assign(option.href);
        }
        return;
      }
      const label = option.dataset.label ?? option.textContent.trim();
      this.setValue(option.dataset.value ?? "", label, true, { ...option.dataset }, "list");
      this.close();
    },
    setValue(value, label, announce, data = {}, source = "input") {
      this.value = value;
      this.selectedLabel = value === "" ? "" : label;
      if (this.valueField) this.valueField.value = value;
      if (value !== "") {
        this.text = label;
        this.$refs.input.value = label;
        this.dirty = true;
      }
      if (value !== "" && announce) {
        emit(this.$root, "combobox:select", {
          id: this.$refs.input.id,
          name: this.valueField ? this.valueField.name : "",
          value,
          label,
          data,
          source,
        });
      } else if (value === "") {
        emit(this.$root, "combobox:clear", {
          id: this.$refs.input.id,
          name: this.valueField ? this.valueField.name : "",
        });
      }
    },
    clear() {
      this.text = "";
      this.$refs.input.value = "";
      this.dirty = true;
      this.detected = false;
      if (this.value !== "") this.setValue("", "", false);
      this.close();
      this.$refs.input.focus();
      if (this.minChars === 0) this.requestSuggestions();
    },
    onFocusOut(event) {
      if (event.relatedTarget && this.$root.contains(event.relatedTarget)) return;
      this.focused = false;
      this.close();
      if (this.mode !== "pick") return;
      if (this.value !== "") {
        this.text = this.selectedLabel;
        this.$refs.input.value = this.selectedLabel;
        return;
      }
      // Typing a label exactly is as good as picking it.
      const typed = normalize(this.text.trim());
      const exact = typed ? this.options().filter((option) => normalize(option.dataset.label ?? "") === typed) : [];
      if (exact.length === 1) this.choose(exact[0]);
    },

    // ---- coordination with other components ---------------------------------
    onExternalSet(event) {
      const { value = "", label = "", onlyIfEmpty = false } = event.detail ?? {};
      if (onlyIfEmpty) {
        const userOwned = this.dirty && !this.detected;
        if (userOwned || (this.value !== "" && !this.detected)) return;
      }
      this.setValue(value, label, true, {}, "external");
      this.detected = onlyIfEmpty;
      this.dirty = false;
    },
    onSiblingChange(event) {
      if (!this.dependsOn || event.detail?.id !== this.dependsOn) return;
      this.text = "";
      this.$refs.input.value = "";
      this.close();
      if (this.value !== "") this.setValue("", "", false);
      this.syncDisabled();
    },
  }));
}
