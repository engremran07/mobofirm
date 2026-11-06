"""PageSpeed Insights adapter (lab and field Core Web Vitals for one URL)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.conf import settings

from apps.seo import fetcher as fetcher_module
from apps.seo.models import SEOSettings

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
HOSTS = frozenset({"www.googleapis.com"})


class PageSpeedError(Exception):
    pass


@dataclass(frozen=True)
class PageSpeedReport:
    url: str
    strategy: str
    performance_score: float | None
    lab: dict[str, float] = field(default_factory=dict)
    field_data: dict[str, float] = field(default_factory=dict)


def api_key() -> str:
    return str(getattr(settings, "SEO_PAGESPEED_API_KEY", "") or "")


def status() -> tuple[str, str]:
    if not SEOSettings.get_solo().pagespeed_enabled:
        return "disabled", "Switched off in SEO settings."
    if not api_key():
        return "not_configured", "Set SEO_PAGESPEED_API_KEY to enable requests."
    return "ready", "Ready; requests are made on demand for a given URL."


def run(url: str, strategy: str = "mobile") -> PageSpeedReport:
    state, detail = status()
    if state != "ready":
        raise PageSpeedError(detail)
    query = urlencode(
        {"url": url, "strategy": strategy, "category": "performance", "key": api_key()}
    )
    try:
        response = fetcher_module.default_fetcher().fetch(
            f"{ENDPOINT}?{query}", allowed_hosts=HOSTS
        )
    except fetcher_module.FetchError as error:
        raise PageSpeedError(str(error)) from error
    if response.status != 200:
        raise PageSpeedError(f"PageSpeed API returned HTTP {response.status}.")
    try:
        payload: dict[str, Any] = json.loads(response.text)
    except json.JSONDecodeError as error:
        raise PageSpeedError("PageSpeed API returned invalid JSON.") from error
    lighthouse = payload.get("lighthouseResult", {})
    score = lighthouse.get("categories", {}).get("performance", {}).get("score")
    audits = lighthouse.get("audits", {})
    lab = {
        name: float(audits[audit]["numericValue"])
        for name, audit in (
            ("lcp_ms", "largest-contentful-paint"),
            ("cls", "cumulative-layout-shift"),
            ("tbt_ms", "total-blocking-time"),
        )
        if audit in audits and "numericValue" in audits[audit]
    }
    field_metrics = payload.get("loadingExperience", {}).get("metrics", {})
    field_data = {
        key.lower(): float(metric["percentile"])
        for key, metric in field_metrics.items()
        if "percentile" in metric
    }
    return PageSpeedReport(
        url=url,
        strategy=strategy,
        performance_score=float(score) if score is not None else None,
        lab=lab,
        field_data=field_data,
    )
