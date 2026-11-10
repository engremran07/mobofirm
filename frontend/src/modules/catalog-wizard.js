// Upload wizard: marks the stepper (brand, model, variant, file, review) as the visitor
// fills the single server-validated form, and keeps the review summary current.
// Purely presentational; the form posts and validates exactly as it does without it.

const ORDER = ["brand", "model", "variant", "file", "review"];

function value(form, selector) {
  const field = form.querySelector(selector);
  return field instanceof HTMLInputElement || field instanceof HTMLSelectElement ? field.value.trim() : "";
}

function label(form, selector) {
  return value(form, selector);
}

function fileLabel(form) {
  const source = form.querySelector('input[name="source"]:checked');
  if (source && source.value === "url") return value(form, 'input[name="external_url"]');
  const file = form.querySelector('input[type="file"][name="file"]');
  return file instanceof HTMLInputElement && file.files && file.files.length ? file.files[0].name : "";
}

function selectedText(form, selector) {
  const select = form.querySelector(selector);
  if (select instanceof HTMLSelectElement) return select.selectedOptions[0]?.textContent?.trim() || "";
  return "";
}

function snapshot(form) {
  const brand = value(form, "#upload-brand-value");
  const model = value(form, "#upload-model-value");
  const noVariant = Boolean(form.querySelector("[data-no-variant]"));
  const variant = noVariant ? "" : value(form, "#upload-variant-value");
  return {
    brand,
    model,
    variantDone: Boolean(model) && (noVariant || Boolean(variant)),
    variantText: noVariant ? "No specific variant" : label(form, "#upload-variant"),
    file: fileLabel(form),
  };
}

function update(form) {
  const state = snapshot(form);
  const done = {
    brand: Boolean(state.brand),
    model: Boolean(state.model),
    variant: state.variantDone,
    file: Boolean(state.file),
  };
  done.review = done.brand && done.model && done.variant && done.file;
  const current = ORDER.find((step) => !done[step]) || "review";
  document.querySelectorAll("[data-wizard-steps] [data-step]").forEach((item) => {
    const step = item.getAttribute("data-step") || "";
    item.classList.toggle("is-done", Boolean(done[step]) && step !== "review");
    item.classList.toggle("is-current", step === current);
    if (step === current) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  const text = {
    brand: label(form, "#upload-brand"),
    model: label(form, "#upload-model"),
    variant: state.variantText,
    file: state.file,
    build: value(form, 'input[name="build_number"]'),
    access: selectedText(form, 'select[name="access_tier"]'),
  };
  document.querySelectorAll("[data-wizard-review] [data-review]").forEach((node) => {
    const key = node.getAttribute("data-review") || "";
    const row = node.closest("div");
    node.textContent = text[key] || "—";
    if (row) row.hidden = key === "access" && !text.access;
  });
}

export function initializeCatalogWizard() {
  const form = document.querySelector("[data-upload-form]");
  if (!(form instanceof HTMLFormElement)) return;
  let pending = 0;
  const schedule = () => {
    window.cancelAnimationFrame(pending);
    pending = window.requestAnimationFrame(() => update(form));
  };
  ["input", "change", "combobox:select", "combobox:clear", "htmx:afterSwap"].forEach((name) => {
    form.addEventListener(name, schedule);
  });
  update(form);
}
