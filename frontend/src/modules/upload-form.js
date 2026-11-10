// Upload page: shows only the inputs that apply to the chosen source (file or link),
// the archive password when the archive is protected, and the price for paid files.
// A change of brand or model re-renders the variant field (required only when the
// model has active variants).
// State is read from the form on start so a re-rendered form with errors stays correct.
// Every expression used by the template is a property reference (Alpine CSP build).

export function registerUploadForm(Alpine) {
  Alpine.data("uploadForm", () => ({
    source: "file",
    protectedArchive: false,
    tier: "free",
    lastModel: "",
    refreshTimer: 0,

    init() {
      const form = this.$root;
      const model = form.querySelector("#upload-model-value");
      this.lastModel = model instanceof HTMLInputElement ? model.value : "";
      const checked = form.querySelector('input[name="source"]:checked');
      this.source = checked ? checked.value : "file";
      const password = form.querySelector('input[name="is_password_protected"]');
      this.protectedArchive = Boolean(password && password.checked);
      const tier = form.querySelector('[name="access_tier"]');
      this.tier = tier ? tier.value : "free";
      this.syncPrice();
    },
    get fileHidden() {
      return this.source !== "file";
    },
    get urlHidden() {
      return this.source !== "url";
    },
    get passwordHidden() {
      return !this.protectedArchive;
    },
    get priceHidden() {
      return this.tier !== "paid";
    },
    // A paid file needs an amount above zero; the field is only required (and so only
    // validated by the inline-hint module) while the Paid tier is chosen.
    syncPrice() {
      const price = this.$root.querySelector('[name="proposed_price"]');
      if (!(price instanceof HTMLInputElement)) return;
      const paid = this.tier === "paid";
      price.required = paid;
      price.setAttribute("aria-required", paid ? "true" : "false");
      if (!paid) price.dispatchEvent(new CustomEvent("mf:revalidate", { bubbles: true }));
    },
    // A new brand or model changes which variants apply: ask the server to
    // re-render the variant field (HTMX listens for this event on the region).
    // Events of one pick arrive in a burst (clear, then select), so they are
    // coalesced into a single request, and nothing is sent when the model is unchanged.
    onPick(event) {
      const id = event.detail?.id;
      if (id !== "upload-brand" && id !== "upload-model") return;
      window.clearTimeout(this.refreshTimer);
      this.refreshTimer = window.setTimeout(() => this.refreshVariant(), 120);
    },
    // HTMX parses a swapped fragment with scripting disabled, so the combobox's
    // <noscript> fallback input would become a real (empty, duplicate) `variant`
    // field and override the picked value on submit. Drop it after each swap.
    onSwap(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement) || target.id !== "upload-variant-region") return;
      target.querySelectorAll("noscript").forEach((node) => node.remove());
    },
    refreshVariant() {
      const model = this.$root.querySelector("#upload-model-value");
      const current = model instanceof HTMLInputElement ? model.value : "";
      if (current === this.lastModel) return;
      this.lastModel = current;
      const region = this.$root.querySelector("#upload-variant-region");
      if (region) region.dispatchEvent(new CustomEvent("variant:refresh"));
    },
    onChange(event) {
      const field = event.target;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) return;
      if (field.name === "source") this.source = field.value;
      if (field.name === "is_password_protected") this.protectedArchive = field.checked;
      if (field.name === "access_tier") {
        this.tier = field.value;
        this.syncPrice();
      }
    },
  }));
}
