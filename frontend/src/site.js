import "bootstrap/dist/css/bootstrap.min.css";
import "./styles/main.scss";
import "./styles/ecosystem.css";
import "./styles/marketplace.css";
import "./styles/combobox.css";
import "./styles/catalog.css";
import "./styles/community.css";
import "./styles/promotions.css";
import "./styles/rewarded.css";
import "./styles/ads.css";
import "./styles/editor-content.css";
import htmx from "htmx.org";

import Alpine from "@alpinejs/csp";

import { registerCombobox } from "./modules/combobox.js";
import { registerConsentBanner } from "./modules/consent.js";
import { initializeContent } from "./modules/content-article.js";
import { initializeAppDialogs, initializeDeviceRegistrationDialog, registerDialogs } from "./modules/dialogs.js";
import { initializeCommerce } from "./modules/commerce-ui.js";
import { initializeDeviceIdentity } from "./modules/device-identity.js";
import { initializeFeedback } from "./modules/feedback.js";
import { renderIcons } from "./modules/icons.js";
import { initializeRequestLinks } from "./modules/request-links.js";
import { registerRewardedAds } from "./modules/rewarded-ads.js";
import { initializeShare } from "./modules/share.js";
import { initializeTheme } from "./modules/theme.js";
import { initializeToasts } from "./modules/toasts.js";
import { initializeUi } from "./modules/ui.js";

// Page-specific Alpine components are separate chunks, fetched only when the page
// renders them (x-data present in the initial HTML) so every other page stays lean.
const PAGE_COMPONENTS = [
  ["mfaSetup", () => import("./modules/mfa-setup.js").then((m) => m.registerMfaSetup)],
  ["passwordChecklist", () => import("./modules/password-checklist.js").then((m) => m.registerPasswordChecklist)],
  ["profileLocale", () => import("./modules/profile-locale.js").then((m) => m.registerProfileLocale)],
  ["uploadForm", () => import("./modules/upload-form.js").then((m) => m.registerUploadForm)],
];

window.htmx = htmx;
htmx.config.allowEval = false;
htmx.config.allowScriptTags = false;
htmx.config.includeIndicatorStyles = false;

window.Alpine = Alpine;
registerCombobox(Alpine);
registerConsentBanner(Alpine);
registerDialogs(Alpine);
registerRewardedAds(Alpine);
// Alpine starts as soon as the page's own components are registered; pages without a
// lazy component (the common case) start synchronously, exactly as before.
const pending = PAGE_COMPONENTS.filter(([name]) => document.querySelector(`[x-data^="${name}"]`)).map(
  ([, load]) => load(),
);
if (pending.length === 0) {
  Alpine.start();
} else {
  Promise.all(pending)
    .then((registrations) => registrations.forEach((register) => register(Alpine)))
    .finally(() => Alpine.start());
}
renderIcons();
initializeTheme();
initializeDeviceRegistrationDialog();
void initializeDeviceIdentity();
initializeAppDialogs();
initializeToasts();
initializeFeedback();
initializeUi();
initializeCommerce();
initializeContent();
initializeShare();
initializeRequestLinks();
// Catalogue helpers (filter chips, variant filter, upload stepper) are fetched only on
// the pages that render them.
if (document.querySelector("[data-catalog-filters],[data-variant-scope]")) {
  import("./modules/catalog-filters.js").then((m) => m.initializeCatalogFilters());
}
if (document.querySelector("[data-upload-wizard]")) {
  import("./modules/catalog-wizard.js").then((m) => m.initializeCatalogWizard());
}
// Inline field hints (Constraint Validation API) are fetched wherever a POST form can
// appear, including forms HTMX swaps in later (dialogs).
const INLINE_FORMS = 'form[method="post" i],form[hx-post],form[data-inline-validate]';
const loadInlineValidation = () =>
  import("./modules/inline-validation.js").then((m) => m.initializeInlineValidation());
if (document.querySelector(INLINE_FORMS)) loadInlineValidation();
document.addEventListener("htmx:afterSwap", () => {
  renderIcons();
  if (document.querySelector(INLINE_FORMS)) loadInlineValidation();
});
