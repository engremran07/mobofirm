"""Request-level behaviour driven by the site settings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.middleware.locale import LocaleMiddleware
from django.urls import NoReverseMatch, reverse
from django.utils import timezone, translation

from apps.consent.constants import ANALYTICS
from apps.consent.services import has_consent

from .services import get_site_settings

logger = logging.getLogger(__name__)

# The response attribute Django's CSP middleware reads (see csp_override).
CSP_ATTRIBUTE = "_csp_config"
LANGUAGE_ATTRIBUTE = "LANGUAGE_CODE"
ANALYTICS_SCRIPT_HOSTS = ("https://www.googletagmanager.com",)
ANALYTICS_IMAGE_HOSTS = (
    "https://www.google-analytics.com",
    "https://*.google-analytics.com",
    "https://www.googletagmanager.com",
)
ANALYTICS_CONNECT_HOSTS = (
    "https://www.google-analytics.com",
    "https://*.google-analytics.com",
    "https://analytics.google.com",
    "https://*.analytics.google.com",
    "https://*.googletagmanager.com",
)


class SiteLocaleMiddleware(LocaleMiddleware):
    """Django's locale middleware plus the site default language and time zone.

    The configured default language only applies to visitors who send neither a
    language cookie nor an ``Accept-Language`` header; every explicit choice wins.
    """

    def process_request(self, request: HttpRequest) -> None:
        super().process_request(request)
        snapshot = get_site_settings()
        if not request.COOKIES.get(
            settings.LANGUAGE_COOKIE_NAME
        ) and not request.headers.get("Accept-Language"):
            try:
                language = translation.get_supported_language_variant(
                    snapshot.default_language
                )
            except LookupError:
                logger.warning(
                    "Default language %r is not supported.", snapshot.default_language
                )
            else:
                translation.activate(language)
                setattr(
                    request, LANGUAGE_ATTRIBUTE, translation.get_language() or language
                )
        if snapshot.timezone != settings.TIME_ZONE:
            try:
                timezone.activate(ZoneInfo(snapshot.timezone))
            except ZoneInfoNotFoundError:
                logger.warning("Time zone %r is not available.", snapshot.timezone)

    def process_response(
        self, request: HttpRequest, response: HttpResponseBase
    ) -> HttpResponseBase:
        timezone.deactivate()
        return super().process_response(request, response)


def _extend(
    config: dict[str, list[str]], directive: str, hosts: tuple[str, ...]
) -> None:
    existing = list(config.get(directive, []))
    config[directive] = [*existing, *(host for host in hosts if host not in existing)]


class SiteAnalyticsCspMiddleware:
    """Allow Google Tag Manager/Analytics hosts, and only when an ID is configured.

    Must be listed after ``ContentSecurityPolicyMiddleware``: responses unwind in
    reverse order, so this runs first and hands the extended policy over through
    ``response._csp_config``, the same hook Django's ``csp_override`` uses. Without
    an ID, and for the admin or a view that set its own policy, nothing changes.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if hasattr(response, CSP_ATTRIBUTE) or not self._is_public_html(
            request, response
        ):
            return response
        if not get_site_settings().analytics.enabled or not has_consent(
            request, ANALYTICS
        ):
            return response
        policy: dict[str, list[str]] = {}
        for name, values in settings.SECURE_CSP.items():
            policy[name] = [values] if isinstance(values, str) else list(values)
        _extend(policy, "script-src", ANALYTICS_SCRIPT_HOSTS)
        _extend(policy, "img-src", ANALYTICS_IMAGE_HOSTS)
        _extend(policy, "connect-src", ANALYTICS_CONNECT_HOSTS)
        setattr(response, CSP_ATTRIBUTE, policy)
        return response

    @staticmethod
    def _is_public_html(request: HttpRequest, response: HttpResponse) -> bool:
        if not response.headers.get("Content-Type", "").startswith("text/html"):
            return False
        try:
            admin_root = reverse("admin:index")
        except NoReverseMatch:
            return True
        return not request.path.startswith(admin_root)
