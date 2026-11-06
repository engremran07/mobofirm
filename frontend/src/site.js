import "bootstrap/dist/css/bootstrap.min.css";
import "./styles/site.css";
import "./styles/blog.css";
import "./styles/ecosystem.css";
import htmx from "htmx.org";

import Alpine from "@alpinejs/csp";

import { initializeCountrySearch } from "./modules/country-search.js";
import { initializeAppDialogs, initializeDeviceRegistrationDialog } from "./modules/dialogs.js";
import { initializeFeedback } from "./modules/feedback.js";
import { registerFileManager } from "./modules/filemanager.js";
import { renderIcons } from "./modules/icons.js";
import { initializeSiteSearch } from "./modules/search.js";
import { initializeTheme } from "./modules/theme.js";

window.Alpine = Alpine;
registerFileManager(Alpine);
Alpine.start();

window.htmx = htmx;
htmx.config.allowEval = false;
htmx.config.allowScriptTags = false;
htmx.config.includeIndicatorStyles = false;

renderIcons();
initializeTheme();
initializeDeviceRegistrationDialog();
initializeAppDialogs();
initializeFeedback();
initializeSiteSearch();
initializeCountrySearch();

document.addEventListener("htmx:afterSwap", (event) => {
  renderIcons();
  initializeCountrySearch(event.detail.target);
});
