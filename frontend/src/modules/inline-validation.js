// Inline field hints: every invalid field in a POST form says what is wrong and how to
// fix it, in the field's own error container, instead of a native browser bubble.
//
// * Uses the Constraint Validation API (required, email/url types, min/max/step,
//   minlength/maxlength, pattern) so the rules stay the ones the HTML already states.
// * One container per field: `[data-field-error]` (role="alert", id `<field id>_errors`,
//   referenced by aria-describedby). Server-rendered errors use the same container, so a
//   field never shows two messages. Forms that do not use the shared form components get
//   a container created next to the control.
// * Rules: show on blur, refresh while typing once a message is showing, validate every
//   field on submit, block the submit and focus the first invalid field.
// * Messages can be overridden per field with data-msg-required / -type / -pattern /
//   -min / -max / -step / -minlength / -maxlength.
// * Opt out: `data-native-validation` on the form, `formnovalidate` on a submit button.
//   Forms that are not POST/HTMX forms opt in with `data-inline-validate`.

const FORMS = 'form[method="post" i],form[hx-post],form[data-inline-validate]';
const SKIPPED_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);
const CHECK_TYPES = new Set(["checkbox", "radio"]);
let counter = 0;

function enhanced(form) {
  return (
    form instanceof HTMLFormElement &&
    form.matches(FORMS) &&
    !form.hasAttribute("data-native-validation") &&
    form.method !== "dialog"
  );
}

function formOf(node) {
  const form = node instanceof Element ? node.closest("form") : null;
  return enhanced(form) ? form : null;
}

function isField(node) {
  return (
    (node instanceof HTMLInputElement && !SKIPPED_TYPES.has(node.type)) ||
    node instanceof HTMLSelectElement ||
    node instanceof HTMLTextAreaElement
  );
}

function checkable(field) {
  if (!field.willValidate) return false;
  if (field.closest("[hidden],[inert],[data-dialog-reason]")) return false;
  return field.getClientRects().length > 0 || CHECK_TYPES.has(field.type);
}

function labelOf(field) {
  const text = field.labels?.[0]?.textContent ?? field.getAttribute("aria-label") ?? "";
  return text.replace(/[*:]/g, "").replace(/\(optional\)/i, "").replace(/\s+/g, " ").trim();
}

function pick(field, key, fallback) {
  return field.getAttribute(`data-msg-${key}`) || fallback;
}

// The message for the first broken rule, or "" when the field is fine.
export function messageFor(field) {
  const state = field.validity;
  if (!field.willValidate || state.valid) return "";
  const label = labelOf(field);
  const named = label ? ` for “${label}”` : "";
  const type = field.type;
  if (state.valueMissing) {
    let fallback = `Please fill in this field${named}.`;
    if (type === "checkbox") fallback = `Please tick “${label || "this box"}” to continue.`;
    else if (type === "radio" || field instanceof HTMLSelectElement) fallback = `Please choose an option${named}.`;
    else if (type === "file") fallback = "Please choose a file.";
    else if (label) fallback = `${label} is required. Please fill it in.`;
    return pick(field, "required", fallback);
  }
  if (state.typeMismatch) {
    const fallback =
      type === "email"
        ? "Enter a valid email address, like name@example.com."
        : type === "url"
          ? "Enter a full web address starting with https://, like https://example.com."
          : `Enter a valid value${named}.`;
    return pick(field, "type", fallback);
  }
  if (state.badInput) return pick(field, "type", `Enter a number${named}.`);
  if (state.patternMismatch) {
    return pick(field, "pattern", field.title || `Use the format that is asked for${named}.`);
  }
  if (state.tooShort) {
    return pick(field, "minlength", `Use at least ${field.minLength} characters (you have ${field.value.length}).`);
  }
  if (state.tooLong) {
    return pick(field, "maxlength", `Use at most ${field.maxLength} characters (you have ${field.value.length}).`);
  }
  if (state.rangeUnderflow) return pick(field, "min", `Enter ${field.min} or more.`);
  if (state.rangeOverflow) return pick(field, "max", `Enter ${field.max} or less.`);
  if (state.stepMismatch) return pick(field, "step", `Enter a valid value (steps of ${field.step || 1}).`);
  return pick(field, "type", field.validationMessage || `Check this field${named}.`);
}

function container(field) {
  const scope = field.closest("[data-field]");
  let box = scope?.querySelector("[data-field-error]") ?? null;
  if (!box && field.id) box = document.getElementById(`${field.id}_errors`);
  if (box) return box;
  box = document.createElement("div");
  box.className = "invalid-feedback field-error";
  box.setAttribute("role", "alert");
  box.setAttribute("data-field-error", "");
  box.id = field.id ? `${field.id}_errors` : `mf-field-error-${++counter}`;
  box.hidden = true;
  const anchor =
    field.closest(".mf-choice-group, fieldset, .form-floating, .input-group, .form-check") ?? field;
  anchor.insertAdjacentElement("afterend", box);
  return box;
}

function describe(field, box) {
  const ids = (field.getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean);
  if (!ids.includes(box.id)) {
    ids.push(box.id);
    field.setAttribute("aria-describedby", ids.join(" "));
  }
}

function show(field, message) {
  const box = container(field);
  describe(field, box);
  box.replaceChildren();
  const text = document.createElement("p");
  text.className = "mb-0";
  text.textContent = message;
  box.append(text);
  box.hidden = false;
  box.dataset.source = "client";
  field.setAttribute("aria-invalid", "true");
  field.classList.add("is-invalid");
}

function clear(field) {
  const box = container(field);
  box.replaceChildren();
  box.hidden = true;
  delete box.dataset.source;
  field.removeAttribute("aria-invalid");
  field.classList.remove("is-invalid");
}

// Radios share one verdict per group; the first radio carries it.
function group(field) {
  if (field.type !== "radio" || !field.name) return [field];
  return [...field.form.elements].filter((el) => el instanceof HTMLInputElement && el.type === "radio" && el.name === field.name);
}

// Returns true when the field (or its group) is valid; shows/clears the message.
export function validateField(field) {
  if (!isField(field) || !checkable(field)) return true;
  const members = group(field);
  const lead = members[0];
  const message = members.map((member) => messageFor(member)).find(Boolean) ?? "";
  if (message) show(lead, message);
  else members.forEach(clear);
  return !message;
}

// A combobox pick field keeps its value in a hidden input and its visible input is not
// part of the form, so "required" is checked on the hidden value.
function comboboxes(form) {
  return [...form.querySelectorAll('.combobox[data-mode="pick"] [role="combobox"][aria-required="true"]')].filter(
    (input) => input instanceof HTMLInputElement && !input.disabled && !input.closest("[hidden]"),
  );
}

function validateCombobox(input, report) {
  const root = input.closest(".combobox");
  const value = root?.querySelector('input[type="hidden"]');
  const missing = !(value instanceof HTMLInputElement) || value.value.trim() === "";
  if (!report) return !missing;
  if (missing) {
    const label = labelOf(input) || input.getAttribute("aria-label") || "this field";
    show(input, input.getAttribute("data-msg-required") || `${label} is required. Pick one from the list.`);
  } else {
    clear(input);
  }
  return !missing;
}

function validateForm(form) {
  const invalid = [];
  const seen = new Set();
  for (const field of form.elements) {
    if (!isField(field) || seen.has(field)) continue;
    group(field).forEach((member) => seen.add(member));
    if (!validateField(field)) invalid.push(group(field)[0]);
  }
  for (const input of comboboxes(form)) {
    if (!validateCombobox(input, true)) invalid.push(input);
  }
  invalid.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  return invalid;
}

function onSubmit(event) {
  const form = formOf(event.target);
  if (!form || event.submitter?.hasAttribute("formnovalidate")) return;
  const invalid = validateForm(form);
  if (invalid.length === 0) return;
  // Stop before dialogs.js (confirmations) and htmx see the submit: nothing is sent.
  event.preventDefault();
  event.stopImmediatePropagation();
  invalid[0].focus();
}

function onBlur(event) {
  const field = event.target;
  if (!formOf(field)) return;
  if (isField(field)) validateField(field);
  else if (field instanceof HTMLInputElement && field.getAttribute("role") === "combobox") {
    if (field.closest(".combobox")?.matches('[data-mode="pick"]') && field.getAttribute("aria-required") === "true") {
      validateCombobox(field, true);
    }
  }
}

// While a message is showing (from the server or from an earlier check) keep it current.
function onEdit(event) {
  const field = event.target;
  if (!formOf(field) || !isField(field)) return;
  const showing = container(field).hidden === false || field.getAttribute("aria-invalid") === "true";
  if (showing || event.type === "mf:revalidate") validateField(field);
}

function prepare(root = document) {
  root.querySelectorAll("form").forEach((form) => {
    if (enhanced(form)) form.noValidate = true;
  });
}

let started = false;

export function initializeInlineValidation() {
  if (started) {
    prepare();
    return;
  }
  started = true;
  prepare();
  // `window` capture runs before every document-level submit handler.
  window.addEventListener("submit", onSubmit, true);
  document.addEventListener("focusout", onBlur);
  document.addEventListener("input", onEdit);
  document.addEventListener("change", onEdit);
  document.addEventListener("mf:revalidate", onEdit);
  document.addEventListener("htmx:afterSwap", () => prepare());
}
