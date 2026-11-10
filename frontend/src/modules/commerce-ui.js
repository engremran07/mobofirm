// Progressive enhancements for the commerce pages (docs/05-frontend/design-system.md):
// the billing-period toggle on the pricing page and live offer countdowns. Both pages
// are complete without JavaScript: every package is listed and every end date is text.
const PERIODS = ["monthly", "yearly"];

function applyPeriod(grid, toggle, period) {
  for (const card of grid.querySelectorAll("[data-plan-period]")) {
    const own = card.dataset.planPeriod;
    card.hidden = PERIODS.includes(own) && own !== period;
  }
  for (const chip of toggle.querySelectorAll("[data-plan-filter]")) {
    chip.setAttribute("aria-pressed", String(chip.dataset.planFilter === period));
  }
}

function initializeBillingToggle() {
  const toggle = document.querySelector("[data-billing-toggle]");
  const grid = document.querySelector("[data-plan-grid]");
  if (!toggle || !grid) return;
  const present = PERIODS.filter((period) => grid.querySelector(`[data-plan-period="${period}"]`));
  if (present.length < 2) return;
  for (const chip of toggle.querySelectorAll("[data-plan-filter]")) {
    chip.hidden = !present.includes(chip.dataset.planFilter);
  }
  toggle.hidden = false;
  const current = grid.querySelector(".is-current[data-plan-period]")?.dataset.planPeriod;
  applyPeriod(grid, toggle, PERIODS.includes(current) ? current : present[0]);
  toggle.addEventListener("click", (event) => {
    const chip = event.target instanceof Element ? event.target.closest("[data-plan-filter]") : null;
    if (chip) applyPeriod(grid, toggle, chip.dataset.planFilter);
  });
}

function remaining(ms) {
  const minutes = Math.max(0, Math.floor(ms / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function tickCountdowns() {
  for (const node of document.querySelectorAll("[data-countdown]")) {
    const end = Date.parse(node.dataset.countdown ?? "");
    const label = node.querySelector("[data-countdown-text]");
    if (Number.isNaN(end) || !label) continue;
    const left = end - Date.now();
    if (left <= 0) {
      label.textContent = node.dataset.countdownEnded ?? "Ended";
      node.classList.remove("is-urgent");
      continue;
    }
    label.textContent = `${node.dataset.countdownPrefix ?? "Ends in"} ${remaining(left)}`;
    node.classList.toggle("is-urgent", left < 86400000);
  }
}

let countdownTimer = 0;

function initializeCountdowns() {
  tickCountdowns();
  if (countdownTimer || !document.querySelector("[data-countdown]")) return;
  countdownTimer = window.setInterval(tickCountdowns, 30000);
}

export function initializeCommerce() {
  initializeBillingToggle();
  initializeCountdowns();
  document.addEventListener("htmx:afterSwap", initializeCountdowns);
}
