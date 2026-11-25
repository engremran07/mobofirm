export function registerMobileNav(Alpine) {
  Alpine.data("mobileNav", () => ({
    mobileOpen: false,

    get expandedAttr() {
      return String(this.mobileOpen);
    },
    get isClosed() {
      return !this.mobileOpen;
    },
    toggle() {
      this.mobileOpen = !this.mobileOpen;
    },
    close() {
      this.mobileOpen = false;
    },
  }));
}
