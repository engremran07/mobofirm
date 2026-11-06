"""Google Search Console adapter (search analytics for this site)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

from django.conf import settings

from apps.seo import fetcher as fetcher_module
from apps.seo.models import SEOSettings
from apps.seo.siteurls import site_origin

HOSTS = frozenset({"www.googleapis.com", "searchconsole.googleapis.com"})
BASE = "https://searchconsole.googleapis.com/webmasters/v3/sites"


class SearchConsoleError(Exception):
    pass


@dataclass(frozen=True)
class PerformanceRow:
    key: str
    clicks: float
    impressions: float
    ctr: float
    position: float


def access_token() -> str:
    return str(getattr(settings, "SEO_GSC_ACCESS_TOKEN", "") or "")


def property_url() -> str:
    configured = str(getattr(settings, "SEO_GSC_PROPERTY", "") or "")
    return configured or (f"{site_origin()}/" if site_origin() else "")


def status() -> tuple[str, str]:
    if not SEOSettings.get_solo().search_console_enabled:
        return "disabled", "Switched off in SEO settings."
    if not access_token() or not property_url():
        return (
            "not_configured",
            "Set SEO_GSC_ACCESS_TOKEN (OAuth bearer token) and SEO_GSC_PROPERTY.",
        )
    return "ready", f"Ready for property {property_url()}."


def search_performance(
    start: date, end: date, dimension: str = "page", limit: int = 100
) -> list[PerformanceRow]:
    """Real Search Console rows; raises instead of returning placeholder data."""
    state, detail = status()
    if state != "ready":
        raise SearchConsoleError(detail)
    body = json.dumps(
        {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": [dimension],
            "rowLimit": min(limit, 1000),
        }
    ).encode("utf-8")
    url = f"{BASE}/{quote(property_url(), safe='')}/searchAnalytics/query"
    try:
        response = fetcher_module.default_fetcher().fetch(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token()}",
            },
            body=body,
            follow_redirects=False,
            allowed_hosts=HOSTS,
        )
    except fetcher_module.FetchError as error:
        raise SearchConsoleError(str(error)) from error
    if response.status != 200:
        raise SearchConsoleError(f"Search Console returned HTTP {response.status}.")
    try:
        payload: dict[str, Any] = json.loads(response.text)
    except json.JSONDecodeError as error:
        raise SearchConsoleError("Search Console returned invalid JSON.") from error
    return [
        PerformanceRow(
            key=str(row.get("keys", [""])[0]),
            clicks=float(row.get("clicks", 0)),
            impressions=float(row.get("impressions", 0)),
            ctr=float(row.get("ctr", 0)),
            position=float(row.get("position", 0)),
        )
        for row in payload.get("rows", [])
    ]
