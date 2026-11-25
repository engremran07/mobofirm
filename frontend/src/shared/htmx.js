import htmx from "htmx.org";

export const NO_SWAP = Object.freeze([204, 304, 401, 403, 404, 405, 408, 410, 413, 429, "5xx"]);

htmx.config.implicitInheritance = false;
htmx.config.includeIndicatorCSS = false;
htmx.config.defaultTimeout = 45000;
htmx.config.noSwap = NO_SWAP;

function rejectExpression() {
  throw new Error("HTMX expression evaluation is disabled by the application CSP.");
}

htmx.registerExtension("mobofirm-csp", {
  init(api) {
    api.initSecurity(undefined, rejectExpression, rejectExpression);
  },
});

document.addEventListener("htmx:before:on:init", (event) => event.preventDefault());

export function sanitizeSwapTasks(tasks) {
  for (const task of tasks ?? []) {
    const fragment = task.fragment;
    if (!fragment?.querySelectorAll) continue;

    for (const script of fragment.querySelectorAll("script")) {
      const type = (script.getAttribute("type") ?? "").toLowerCase();
      if (type === "" || type.includes("javascript") || type === "module") {
        script.remove();
      }
    }

    for (const element of fragment.querySelectorAll("*")) {
      for (const { name } of [...element.attributes]) {
        if (/^(on|hx-on|data-hx-on)/i.test(name)) element.removeAttribute(name);
      }
    }
  }
}

export { htmx };
