"""Single source of truth for which social sign-in providers are usable.

The login and sign-up pages always list every provider the project supports, so a
visitor can see the option. Whether the button can start an OAuth flow is decided
here, once, by asking allauth the same question its login views ask: is there an
``SocialApp`` for this provider on the current site, from the database (admin) or
from ``SOCIALACCOUNT_PROVIDERS`` (environment), with a real client id and secret?

Templates read the result through the ``{% social_providers %}`` tag
(``apps/users/templatetags/users_ui.py``); nothing else decides configured-state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.providers import registry
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

# Values admins or setup scripts leave behind before real credentials are known.
PLACEHOLDER_PREFIXES: Final[tuple[str, ...]] = ("your-", "placeholder", "changeme")
SOCIAL_BUTTON_ICON: Final[str] = "globe"


@dataclass(frozen=True)
class SocialProviderState:
    """One provider as the sign-in pages show it."""

    id: str
    name: str
    configured: bool
    login_url: str
    icon: str = SOCIAL_BUTTON_ICON

    @property
    def label(self) -> str:
        """Button text."""
        return f"Continue with {self.name}"

    @property
    def unavailable_message(self) -> str:
        """What an unconfigured provider's button explains."""
        return f"{self.name} sign-in is not set up yet by the site admin."


def is_placeholder(value: str | None) -> bool:
    """Whether a credential is blank or an obvious stand-in rather than a real value."""
    text = (value or "").strip().lower()
    return not text or text.startswith(PLACEHOLDER_PREFIXES)


def credentials_complete(client_id: str | None, secret: str | None) -> bool:
    """Whether an OAuth client id and secret are both real values."""
    return not is_placeholder(client_id) and not is_placeholder(secret)


def provider_configured(request: HttpRequest, provider_id: str) -> bool:
    """Whether ``provider_id`` can start an OAuth flow for this request's site."""
    try:
        apps = get_adapter().list_apps(request, provider=provider_id)
    except (DatabaseError, ObjectDoesNotExist, ImproperlyConfigured):
        logger.exception("Could not read social apps for provider %s", provider_id)
        return False
    return any(credentials_complete(app.client_id, app.secret) for app in apps)


def _login_url(request: HttpRequest, provider_id: str) -> str:
    try:
        url = reverse(f"{provider_id}_login")
    except NoReverseMatch:
        return ""
    params = {"process": "login"}
    candidate = request.GET.get("next", "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        params["next"] = candidate
    return f"{url}?{urlencode(params)}"


def supported_provider_ids() -> list[tuple[str, str]]:
    """``(id, display name)`` of every provider installed in the project."""
    return sorted(
        (
            (provider_class.id, str(provider_class.name))
            for provider_class in registry.get_class_list()
            if provider_class.uses_apps
        ),
        key=lambda item: (item[0] != "google", item[1].lower()),
    )


def social_provider_states(request: HttpRequest) -> list[SocialProviderState]:
    """Every supported provider with its configured-state, Google first."""
    states: list[SocialProviderState] = []
    for provider_id, name in supported_provider_ids():
        login_url = _login_url(request, provider_id)
        if not login_url:
            logger.warning("Social provider %s has no login URL; skipped", provider_id)
            continue
        states.append(
            SocialProviderState(
                id=provider_id,
                name=name,
                configured=provider_configured(request, provider_id),
                login_url=login_url,
            )
        )
    return states
