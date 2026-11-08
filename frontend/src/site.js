import "bootstrap/dist/css/bootstrap.min.css";
import "./styles/site.css";
import "./styles/blog.css";
import "./styles/ecosystem.css";
import "./styles/marketplace.css";
import "./styles/combobox.css";
import "./styles/catalog.css";
import "./styles/community.css";
import "./styles/dialogs.css";
import "./styles/promotions.css";
import "./styles/rewarded.css";
import "./styles/ads.css";
import htmx from "htmx.org";

import Alpine from "@alpinejs/csp";

import { registerCombobox } from "./modules/combobox.js";
import { registerConsentBanner } from "./modules/consent.js";
import { initializeAppDialogs, initializeDeviceRegistrationDialog, registerDialogs } from "./modules/dialogs.js";
import { initializeFeedback } from "./modules/feedback.js";
import { renderIcons } from "./modules/icons.js";
import { registerMfaSetup } from "./modules/mfa-setup.js";
import { registerProfileLocale } from "./modules/profile-locale.js";
import { initializeRequestLinks } from "./modules/request-links.js";
import { registerRewardedAds } from "./modules/rewarded-ads.js";
import { initializeTheme } from "./modules/theme.js";
import { registerUploadForm } from "./modules/upload-form.js";

window.Alpine = Alpine;
registerCombobox(Alpine);
registerConsentBanner(Alpine);
registerDialogs(Alpine);
registerProfileLocale(Alpine);
registerMfaSetup(Alpine);
registerRewardedAds(Alpine);
registerUploadForm(Alpine);
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
initializeRequestLinks();
document.addEventListener("htmx:afterSwap", () => {
  renderIcons();
});
