// MFA setup: the manual setup key stays hidden until the user asks for it, because the
// QR code (rendered server side as inline SVG) is the normal way to enrol.

export function registerMfaSetup(Alpine) {
  Alpine.data("mfaSetup", () => ({
    showKey: false,
    copied: false,
    copyFailed: false,

    get keyHidden() {
      return !this.showKey;
    },
    get expandedAttr() {
      return this.showKey ? "true" : "false";
    },
    get toggleLabel() {
      return this.showKey ? "Hide setup key" : "Can't scan? Show setup key";
    },
    get copyLabel() {
      if (this.copyFailed) return "Select the key and copy it";
      return this.copied ? "Copied" : "Copy key";
    },
    toggle() {
      this.showKey = !this.showKey;
    },
    async copy() {
      const field = this.$refs.key;
      if (!field) return;
      try {
        await navigator.clipboard.writeText(field.textContent.trim());
        this.copied = true;
        this.copyFailed = false;
      } catch (error) {
        console.debug("Clipboard unavailable", error);
        this.copyFailed = true;
      }
    },
  }));
}
