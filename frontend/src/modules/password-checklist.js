// Live password checklist (Alpine CSP component, registered lazily before Alpine starts).
//
// The page renders one row per configured server password validator; this component
// only asks the server (a debounced, CSRF-protected POST to the users app) which rules
// the typed password breaks, so the policy has a single source of truth. The
// "passwords match" row is computed locally from the two inputs.
//
// Privacy: the password is sent only in that POST body (never in a URL or storage,
// never logged); nothing is read back but per-rule booleans.
//
// Rows carry data-state = pending | pass | fail | unchecked. Each state has its own
// icon shape and a visually hidden status word, so meaning never rests on colour, and
// the summary below the list is an aria-live="polite" region.

const DEBOUNCE_MS = 300;
const SIMILARITY_FIELDS = ["username", "email", "full_name"];
const STATUS = {
  pending: ": not met yet",
  pass: ": met",
  fail: ": not met",
  unchecked: ": checked when you submit",
};

function input(form, name) {
  const field = name ? form?.elements.namedItem(name) : null;
  return field instanceof HTMLInputElement ? field : null;
}

export function registerPasswordChecklist(Alpine) {
  Alpine.data("passwordChecklist", () => ({
    form: null,
    password: null,
    confirm: null,
    timer: 0,
    sequence: 0,
    controller: null,
    listeners: [],

    init() {
      const settings = this.$root.dataset;
      this.form = this.$root.closest("form");
      this.password = input(this.form, settings.passwordName);
      this.confirm = input(this.form, settings.confirmName);
      if (!this.password) return;
      this.listen(this.password, () => {
        this.updateMatch();
        this.schedule();
      });
      if (this.confirm) this.listen(this.confirm, () => this.updateMatch());
      for (const name of SIMILARITY_FIELDS) {
        const related = input(this.form, name);
        if (related) this.listen(related, () => this.schedule());
      }
      if (this.password.value) this.check();
    },

    destroy() {
      window.clearTimeout(this.timer);
      this.controller?.abort();
      for (const [target, handler] of this.listeners) target.removeEventListener("input", handler);
      this.listeners = [];
    },

    listen(target, handler) {
      target.addEventListener("input", handler);
      this.listeners.push([target, handler]);
    },

    schedule() {
      window.clearTimeout(this.timer);
      if (!this.password.value) {
        this.reset();
        return;
      }
      this.timer = window.setTimeout(() => this.check(), DEBOUNCE_MS);
    },

    rows() {
      return [...this.$root.querySelectorAll("[data-rule]")];
    },

    setState(row, state) {
      row.dataset.state = state;
      const status = row.querySelector("[data-rule-status]");
      if (status) status.textContent = STATUS[state];
    },

    reset() {
      this.sequence += 1;
      this.controller?.abort();
      for (const row of this.rows()) this.setState(row, "pending");
      this.summarize("");
    },

    updateMatch() {
      const row = this.$root.querySelector('[data-rule="match"]');
      if (!row || !this.confirm) return;
      const typed = this.password.value;
      const again = this.confirm.value;
      let state = "pending";
      if (typed && again) state = typed === again ? "pass" : "fail";
      this.setState(row, state);
      this.summarize();
    },

    async check() {
      const body = new URLSearchParams({ password: this.password.value });
      for (const name of SIMILARITY_FIELDS) {
        const related = input(this.form, name);
        if (related?.value) body.set(name, related.value);
      }
      const token = this.form.querySelector('input[name="csrfmiddlewaretoken"]')?.value ?? "";
      this.controller?.abort();
      this.controller = new AbortController();
      const sequence = ++this.sequence;
      try {
        const response = await fetch(this.$root.dataset.checkUrl, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          signal: this.controller.signal,
          headers: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": token,
            "X-Requested-With": "XMLHttpRequest",
          },
          body,
        });
        if (sequence !== this.sequence) return;
        if (!response.ok) {
          this.unavailable();
          return;
        }
        this.apply(await response.json());
      } catch (error) {
        if (error.name !== "AbortError") this.unavailable();
      }
    },

    apply(result) {
      for (const rule of result.rules ?? []) {
        const row = this.$root.querySelector(`[data-rule="${CSS.escape(rule.id)}"]`);
        if (!row) continue;
        this.setState(row, !rule.passed ? "fail" : rule.checked === false ? "unchecked" : "pass");
      }
      this.updateMatch();
      this.summarize();
    },

    // The server could not be asked (offline, rate limited): say so; the server still
    // enforces every rule when the form is submitted.
    unavailable() {
      for (const row of this.rows()) {
        if (row.dataset.rule !== "match") this.setState(row, "unchecked");
      }
      this.summarize("The requirements could not be checked right now. They are checked again when you submit.");
    },

    summarize(override) {
      const summary = this.$root.querySelector("[data-pw-summary]");
      if (!summary) return;
      if (override !== undefined) {
        summary.textContent = override;
        return;
      }
      const rows = this.rows();
      const met = rows.filter((row) => row.dataset.state === "pass").length;
      const failed = rows.filter((row) => row.dataset.state === "fail").length;
      summary.textContent = this.password.value
        ? failed === 0 && met === rows.length
          ? "All password requirements are met."
          : `${met} of ${rows.length} password requirements met.`
        : "";
    },
  }));
}
