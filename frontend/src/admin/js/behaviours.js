// Hook scanner: attaches the behaviour modules to their root elements, once per element,
// on first load and after every htmx swap (including content that lands in dialogs).
// Roots that left the DOM are released so listeners never outlive their markup.
import { attachChangelist } from "./components/changelist.js";
import { attachCombobox } from "./components/combobox.js";
import { attachConditional } from "./components/conditional.js";
import { attachFormset } from "./components/formset.js";
import { attachPrepopulated } from "./components/prepopulated.js";
import { attachTabs } from "./components/tabs.js";
import { bindOnce, unbind } from "./lib/util.js";

const REGISTRY = [
  ["[data-console-changelist]", attachChangelist],
  ["[data-console-formset]", attachFormset],
  ["[data-console-tabs]", attachTabs],
  ["[data-console-combobox]", attachCombobox],
  ["[data-console-prepopulated]", attachPrepopulated],
  ["script#console-conditional-rules", attachConditional],
];

const bound = new Set();

export function scan(root = document) {
  for (const element of [...bound]) {
    if (!element.isConnected) {
      unbind(element);
      bound.delete(element);
    }
  }
  for (const [selector, attach] of REGISTRY) {
    for (const element of root.querySelectorAll(selector)) {
      if (bound.has(element)) continue;
      bound.add(element);
      bindOnce(element, attach);
    }
  }
}

export function initBehaviours() {
  scan();
  document.addEventListener("htmx:after:swap", () => scan());
}
