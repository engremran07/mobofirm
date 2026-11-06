"""One place that reports every integration's real state to the dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from apps.seo.models import SEOSettings
from apps.seo.siteurls import site_origin

from . import pagespeed, search_console


@dataclass(frozen=True)
class IntegrationStatus:
    key: str
    label: str
    state: str
    detail: str


def indexnow_status() -> tuple[str, str]:
    row = SEOSettings.get_solo()
    if not row.indexnow_enabled:
        return "disabled", "Switched off in SEO settings."
    if not row.indexnow_key:
        return "not_configured", "Generate or enter an IndexNow key."
    if not site_origin():
        return "not_configured", "Configure SITE_URL so the key file URL is public."
    return "ready", "Changed public URLs are submitted in Safe autopilot and Full."


def integration_statuses() -> list[IntegrationStatus]:
    rows = [
        ("indexnow", "IndexNow (Bing, Yandex, Seznam, Naver)", indexnow_status()),
        ("search_console", "Google Search Console", search_console.status()),
        ("pagespeed", "PageSpeed Insights", pagespeed.status()),
        (
            "bing",
            "Bing Webmaster Tools",
            (
                "unsupported",
                "Legacy Bing APIs are retired; Bing is notified by IndexNow.",
            ),
        ),
    ]
    return [IntegrationStatus(key, label, *state) for key, label, state in rows]
