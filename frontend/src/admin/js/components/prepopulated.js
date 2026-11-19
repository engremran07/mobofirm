// Slug prefill (templates/admin/prepopulated_fields_js.html): a data block, never executable script.
//   <div hidden data-console-prepopulated='[{"id","name","dependency_ids","dependency_list","maxLength","allowUnicode"}]'>
// The target gets a slug of its dependencies while the person has not typed into it (or it
// already had a value). Inline rows added later resolve their `__prefix__` ids through the
// formset-added event.
import { EVENTS } from "../lib/util.js";

const bare = (id) => String(id).replace(/^#/, "");

export function slugify(value, maxLength, allowUnicode) {
  let text = String(value).normalize(allowUnicode ? "NFKC" : "NFKD");
  if (!allowUnicode) text = text.replace(/[\u0300-\u036f]/g, "").replace(/[^\x00-\x7f]/g, "");
  text = text
    .toLowerCase()
    .replace(allowUnicode ? /[^\p{L}\p{N}\s_-]/gu : /[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (maxLength > 0 && text.length > maxLength) text = text.slice(0, maxLength).replace(/-+$/, "");
  return text;
}

function parse(root) {
  try {
    const rules = JSON.parse(root.getAttribute("data-console-prepopulated") ?? "[]");
    return Array.isArray(rules) ? rules : [];
  } catch {
    return [];
  }
}

export function attachPrepopulated(root) {
  const controller = new AbortController();
  const { signal } = controller;
  const wired = new WeakSet();

  function wire(rule, index) {
    const target = document.getElementById(bare(rule.id).replace("__prefix__", index));
    const sources = (rule.dependency_ids ?? [])
      .map((id) => document.getElementById(bare(id).replace("__prefix__", index)))
      .filter((element) => element instanceof HTMLElement);
    if (!(target instanceof HTMLInputElement) || sources.length === 0 || wired.has(target)) return;
    wired.add(target);
    let edited = target.value !== "";
    target.addEventListener("input", () => (edited = target.value !== ""), { signal });
    const populate = () => {
      if (edited) return;
      const joined = sources.map((source) => source.value).filter(Boolean).join(" ");
      target.value = slugify(joined, Number(rule.maxLength) || 0, Boolean(rule.allowUnicode));
    };
    for (const source of sources) {
      source.addEventListener("input", populate, { signal });
      source.addEventListener("change", populate, { signal });
    }
  }

  const rules = parse(root);
  for (const rule of rules) {
    if (!String(rule.id).includes("__prefix__")) wire(rule, "");
  }
  document.addEventListener(
    EVENTS.formsetAdded,
    (event) => {
      const index = event.detail?.index;
      if (index === undefined) return;
      for (const rule of rules) {
        if (String(rule.id).includes("__prefix__")) wire(rule, String(index));
      }
    },
    { signal },
  );
  return { destroy: () => controller.abort() };
}
