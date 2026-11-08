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

const normalize = (text) => text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

function emit(root, type, detail) {
  root.dispatchEvent(new CustomEvent(type, { bubbles: true, detail }));
}

export function registerCombobox(Alpine) {
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
    status: "",

    init() {
      const settings = this.$root.dataset;
      this.mode = settings.mode === "navigate" ? "navigate" : "pick";
      this.minChars = Number.parseInt(settings.minChars ?? "0", 10) || 0;
      this.dependsOn = settings.dependsOn ?? "";
      this.dependsParam = settings.dependsParam ?? "";
      this.detected = settings.detected === "true";
      this.text = this.$refs.input.value;
      this.value = this.valueField ? this.valueField.value : "";
      this.selectedLabel = this.value ? this.text : "";
      this.syncDisabled();
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
      if (this.text.trim().length < this.minChars) this.close();
    },
    onFocus() {
      this.focused = true;
      // A chosen value is replaced, not extended: select it so typing overwrites it.
      if (this.mode === "pick" && this.value !== "") this.$refs.input.select();
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
        if (tooShort || blocked) this.close();
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
      if (this.mode === "pick") {
        event.preventDefault();
        this.choose(option);
      }
    },
    choose(option) {
      if (this.mode === "navigate") {
        if (option instanceof HTMLAnchorElement) window.location.assign(option.href);
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
