// Profile-completion helpers (Alpine CSP components registered before Alpine starts).
//
// `profileLocale` auto-populates the empty country and calling-code comboboxes from
// the browser's time zone and language, resolved by the server's static tables
// (`/users/locale-hint/`), so it works on a local 127.0.0.1 server where IP detection
// is impossible. It only fills EMPTY fields (or values the server pre-filled from a
// weak hint) and never overrides what the user typed or picked.

function comboboxRoot(inputId) {
  const input = document.getElementById(inputId);
  return input ? input.closest("[data-mode]") : null;
}

function setCombobox(inputId, value, label) {
  const root = comboboxRoot(inputId);
  if (!root || !value) return;
  root.dispatchEvent(
    new CustomEvent("combobox:set", { detail: { value, label, onlyIfEmpty: true } }),
  );
}

export function registerProfileLocale(Alpine) {
  Alpine.data("profileLocale", () => ({
    hintText: "",
    hintVisible: false,
    countryId: "",
    codeId: "",

    init() {
      const settings = this.$root.dataset;
      this.countryId = settings.countryId ?? "";
      this.codeId = settings.codeId ?? "";
      this.hintText = settings.detectedText ?? "";
      this.hintVisible = this.hintText !== "";
      // A trusted edge header is authoritative; otherwise ask what the browser knows.
      if (settings.detectedSource !== "edge") this.detectFromBrowser(settings.hintUrl ?? "");
    },

    get hintHidden() {
      return !this.hintVisible;
    },

    async detectFromBrowser(url) {
      if (!url) return;
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone ?? "";
      const query = new URLSearchParams({ tz: zone, lang: navigator.language ?? "" });
      try {
        const response = await fetch(`${url}?${query}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
        });
        if (!response.ok) return;
        const hint = await response.json();
        if (hint.detected) this.apply(hint);
      } catch (error) {
        console.debug("Location hint unavailable", error);
      }
    },

    apply(hint) {
      setCombobox(this.countryId, hint.country, hint.country_label);
      setCombobox(this.codeId, hint.calling_code, hint.calling_label);
      const country = document.getElementById(this.countryId);
      const value = country ? document.getElementById(`${this.countryId}-value`) : null;
      if (value && value.value === hint.country) {
        this.hintText = `Detected: ${hint.country_label}${hint.calling_code ? `, ${hint.calling_code}` : ""} —`;
        this.hintVisible = true;
      }
    },

    // Choosing a country by hand offers its calling code when that field is still empty.
    onSelect(event) {
      const { id, value, data, source } = event.detail;
      if (id !== this.countryId || source === "external") return;
      this.hintVisible = false;
      const code = data.callingCode ?? "";
      if (code) {
        const label = `${event.detail.label} ${code}`;
        setCombobox(this.codeId, code, label);
      }
    },

    change() {
      const input = document.getElementById(this.countryId);
      if (input) {
        input.focus();
        input.select();
      }
    },
  }));
}
