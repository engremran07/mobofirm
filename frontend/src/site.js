import "bootstrap/dist/css/bootstrap.min.css";
import "./styles/site.css";
import htmx from "htmx.org";

import Alpine from "@alpinejs/csp";
import {
  ArrowDown,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Cpu,
  Download,
  ExternalLink,
  FileSearch,
  Filter,
  Menu,
  Moon,
  Newspaper,
  Search,
  ShieldCheck,
  Smartphone,
  Sun,
  Tags,
  UserRound,
  X,
  createIcons,
} from "lucide";

const iconSet = {
  ArrowDown,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Cpu,
  Download,
  ExternalLink,
  FileSearch,
  Filter,
  Menu,
  Moon,
  Newspaper,
  Search,
  ShieldCheck,
  Smartphone,
  Sun,
  Tags,
  UserRound,
  X,
};

window.Alpine = Alpine;
Alpine.start();

window.htmx = htmx;
htmx.config.allowEval = false;
htmx.config.allowScriptTags = false;
htmx.config.includeIndicatorStyles = false;

function renderIcons() {
  createIcons({ icons: iconSet, attrs: { "stroke-width": 1.8 } });
}

function initializeCountrySearch(root = document) {
  root.querySelectorAll("[data-country-search-for]").forEach((input) => {
    if (input.dataset.countrySearchReady) return;

    const select = document.getElementById(input.dataset.countrySearchFor);
    if (!select) return;

    input.dataset.countrySearchReady = "true";
    const status = document.getElementById(`${input.id}-status`);
    const options = Array.from(select.options);
    const normalize = (value) =>
      value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();

    const updateOptions = () => {
      const query = normalize(input.value.trim());
      let matches = 0;

      options.forEach((option) => {
        const searchableText = normalize(`${option.textContent} ${option.value}`);
        const visible = option.value === "" || !query || searchableText.includes(query);
        option.hidden = !visible;
        if (option.value && visible) matches += 1;
      });

      if (status) {
        status.textContent = query
          ? `${matches} matching options`
          : `${matches} options available`;
      }
    };

    input.addEventListener("input", updateOptions);
    updateOptions();
  });
}

renderIcons();
document.addEventListener("htmx:afterSwap", renderIcons);
initializeCountrySearch();
document.addEventListener("htmx:afterSwap", (event) =>
  initializeCountrySearch(event.detail.target),
);

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-theme-toggle]");
  if (!toggle) return;

  const current = document.documentElement.dataset.bsTheme || "light";
  const next = current === "dark" ? "light" : "dark";
  setTheme(next);
});

function setTheme(theme) {
  document.documentElement.dataset.bsTheme = theme;
  localStorage.setItem("mobofirm-theme", theme);
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    const targetTheme = theme === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", `Switch to ${targetTheme} theme`);
  });
  renderIcons();
}

const savedTheme = localStorage.getItem("mobofirm-theme");
setTheme(savedTheme === "dark" ? "dark" : "light");

document.addEventListener("htmx:responseError", () => {
  const feedback = document.getElementById("global-feedback");
  if (feedback) {
    feedback.textContent = "That request could not be completed. Please try again.";
    feedback.setAttribute("role", "alert");
  }
});

document.addEventListener("htmx:afterRequest", (event) => {
  if (!event.detail.failed) return;
  const feedback = document.getElementById("global-feedback");
  if (feedback) {
    feedback.textContent = "That request could not be completed. Please try again.";
    feedback.setAttribute("role", "alert");
  }
});
