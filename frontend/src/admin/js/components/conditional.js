// Conditional fields: `<script type="application/json" id="console-conditional-rules">` (json_script)
//   [{"field": target, "when": [{"field": other, "op": "eq"|"ne", "value": true|false|"text"|number}, ...]}]
// The target's wrapper [data-console-field="<target>"] is shown only while every clause
// holds. Nothing is ever evaluated: the rules are plain data. The fields stay in the form
// (hidden, not disabled), so the server keeps validating exactly what it did before.
const controls = (form, name) => [...form.querySelectorAll(`[name="${CSS.escape(name)}"]`)];

function currentValue(form, name) {
  const fields = controls(form, name);
  const first = fields[0];
  if (!first) return null;
  if (first instanceof HTMLInputElement && first.type === "radio") return fields.find((field) => field.checked)?.value ?? "";
  if (first instanceof HTMLInputElement && first.type === "checkbox" && fields.length === 1) return first.checked;
  if (first instanceof HTMLSelectElement && first.multiple) return [...first.selectedOptions].map((option) => option.value);
  return first.value;
}

function holds(clause, form) {
  const actual = currentValue(form, clause.field);
  let equal;
  if (typeof clause.value === "boolean") {
    equal = typeof actual === "boolean" ? actual === clause.value : (String(actual).toLowerCase() === "true") === clause.value;
  } else if (Array.isArray(actual)) {
    equal = actual.includes(String(clause.value));
  } else {
    equal = String(actual ?? "") === String(clause.value);
  }
  return clause.op === "ne" ? !equal : equal;
}

export function attachConditional(root) {
  let rules = [];
  try {
    rules = JSON.parse(root.textContent ?? "[]");
  } catch {
    return { destroy() {} };
  }
  if (!Array.isArray(rules) || rules.length === 0) return { destroy() {} };
  const form = root.closest("form") ?? document;
  const controller = new AbortController();

  function apply() {
    for (const rule of rules) {
      const visible = (rule.when ?? []).every((clause) => holds(clause, form));
      for (const wrapper of form.querySelectorAll(`[data-console-field="${CSS.escape(rule.field)}"]`)) {
        wrapper.hidden = !visible;
        wrapper.toggleAttribute("data-conditional-hidden", !visible);
      }
    }
  }

  form.addEventListener("input", apply, { signal: controller.signal });
  form.addEventListener("change", apply, { signal: controller.signal });
  apply();
  return { destroy: () => controller.abort() };
}
