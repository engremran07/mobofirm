// Consent banner: the "Customise" link normally opens the privacy centre; with
// JavaScript it expands the category choices inside the banner instead.

export function registerConsentBanner(Alpine) {
  Alpine.data("consentBanner", () => ({
    open: false,

    get panelHidden() {
      return !this.open;
    },
    get expandedAttr() {
      return this.open ? "true" : "false";
    },
    toggle() {
      this.open = !this.open;
    },
  }));
}
