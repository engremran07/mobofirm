// Upload page: shows only the inputs that apply to the chosen source (file or link),
// the archive password when the archive is protected, and the price for paid files.
// State is read from the form on start so a re-rendered form with errors stays correct.
// Every expression used by the template is a property reference (Alpine CSP build).

export function registerUploadForm(Alpine) {
  Alpine.data("uploadForm", () => ({
    source: "file",
    protectedArchive: false,
    tier: "free",

    init() {
      const form = this.$root;
      const checked = form.querySelector('input[name="source"]:checked');
      this.source = checked ? checked.value : "file";
      const password = form.querySelector('input[name="is_password_protected"]');
      this.protectedArchive = Boolean(password && password.checked);
      const tier = form.querySelector('[name="access_tier"]');
      this.tier = tier ? tier.value : "free";
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
    onChange(event) {
      const field = event.target;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) return;
      if (field.name === "source") this.source = field.value;
      if (field.name === "is_password_protected") this.protectedArchive = field.checked;
      if (field.name === "access_tier") this.tier = field.value;
    },
  }));
}
