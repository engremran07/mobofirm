// File manager components. Registered before Alpine starts; every expression used by the
// templates is a property or method reference, which the CSP build can evaluate.

export function registerFileManager(Alpine) {
  Alpine.data("fmDropzone", () => ({
    dragging: false,
    busy: false,
    names: [],
    progress: 0,
    init() {
      const form = this.$root.querySelector("form");
      form.addEventListener("htmx:xhr:loadstart", () => {
        this.busy = true;
        this.progress = 0;
      });
      form.addEventListener("htmx:xhr:progress", (event) => {
        const { loaded, total, lengthComputable } = event.detail;
        if (lengthComputable && total > 0) this.progress = Math.round((loaded / total) * 100);
      });
      form.addEventListener("htmx:afterRequest", () => {
        this.busy = false;
      });
    },
    get dragClass() {
      return this.dragging ? "is-dragging" : "";
    },
    get summary() {
      if (this.busy) return `Uploading ${this.names.length} file(s)… ${this.progress}%`;
      if (this.names.length === 0) return "Drag files here or choose them below.";
      return `${this.names.length} file(s) ready: ${this.names.join(", ")}`;
    },
    onDragOver() {
      this.dragging = true;
    },
    onDragLeave() {
      this.dragging = false;
    },
    onDrop(event) {
      this.dragging = false;
      const files = event.dataTransfer ? event.dataTransfer.files : null;
      if (!files || files.length === 0) return;
      this.$root.querySelector("input[type=file]").files = files;
      this.onPick();
      this.$root.querySelector("form").requestSubmit();
    },
    onPick() {
      this.names = Array.from(this.$root.querySelector("input[type=file]").files, (file) => file.name);
    },
  }));

  Alpine.data("fmCopy", () => ({
    copied: false,
    failed: false,
    get label() {
      if (this.failed) return "Select the link and copy it";
      return this.copied ? "Copied" : "Copy link";
    },
    async copy() {
      const field = document.getElementById(this.$el.dataset.copyTarget);
      if (!field) return;
      try {
        await navigator.clipboard.writeText(field.value);
        this.copied = true;
        this.failed = false;
      } catch {
        field.select();
        this.failed = true;
      }
    },
  }));
}
