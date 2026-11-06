/*
 * Same-origin analytics loader. The page only carries data attributes; nothing is
 * executed inline. It is a no-op when the visitor sends Do Not Track or Global
 * Privacy Control, and it is only emitted at all when an ID is configured.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) {
    return;
  }
  var optedOut =
    navigator.doNotTrack === "1" ||
    window.doNotTrack === "1" ||
    navigator.globalPrivacyControl === true;
  if (optedOut) {
    return;
  }

  var ga4Id = script.getAttribute("data-ga4-id") || "";
  var gtmId = script.getAttribute("data-gtm-id") || "";
  var ga4Valid = /^G-[A-Z0-9]{4,20}$/.test(ga4Id);
  var gtmValid = /^GTM-[A-Z0-9]{4,12}$/.test(gtmId);
  if (!ga4Valid && !gtmValid) {
    return;
  }

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    window.dataLayer.push(arguments);
  }

  function load(src) {
    var element = document.createElement("script");
    element.async = true;
    element.src = src;
    document.head.appendChild(element);
  }

  if (gtmValid) {
    window.dataLayer.push({ "gtm.start": Date.now(), event: "gtm.js" });
    load("https://www.googletagmanager.com/gtm.js?id=" + encodeURIComponent(gtmId));
  }
  if (ga4Valid) {
    gtag("js", new Date());
    gtag("config", ga4Id, { anonymize_ip: true });
    load("https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(ga4Id));
  }
})();
