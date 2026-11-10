"""Template helpers for the sign-in pages: social provider buttons and the live
password checklist. Both read their facts from ``apps.users.services`` so templates
never decide configured-state or password policy themselves."""

from __future__ import annotations

from typing import Any

from django import forms, template
from django.http import HttpRequest
from django.template.context import Context
from django.urls import reverse

from apps.users.services.password_rules import password_rules
from apps.users.services.social_providers import (
    SocialProviderState,
    social_provider_states,
)

register = template.Library()

# BoundField names that hold a NEW password (allauth and the profile onboarding form)
# and the confirmation that must match it.
NEW_PASSWORD_FIELDS = {"password1": "password2"}


@register.simple_tag(takes_context=True)
def social_providers(context: Context) -> list[SocialProviderState]:
    """Every supported social provider with its configured-state (see the service)."""
    request = context.get("request")
    if not isinstance(request, HttpRequest):
        return []
    return social_provider_states(request)


@register.filter
def is_new_password(field: forms.BoundField) -> bool:
    """Whether ``field`` is a new-password input that gets the live checklist."""
    widget = field.field.widget
    return (
        getattr(widget, "input_type", "") == "password"
        and field.name in NEW_PASSWORD_FIELDS
    )


@register.inclusion_tag("users/includes/password_checklist.html")
def password_checklist(
    target: forms.BoundField | str, confirm: str = ""
) -> dict[str, Any]:
    """The live checklist for a new-password input.

    ``target`` is the new-password ``BoundField`` (its confirmation input is found
    on the same form) or, for a hand-written form, the input's ``name`` with the
    confirmation input's name as ``confirm``.
    """
    if isinstance(target, forms.BoundField):
        password_name = target.html_name
        partner = NEW_PASSWORD_FIELDS.get(target.name, "")
        confirm_name = (
            target.form[partner].html_name if partner in target.form.fields else ""
        )
    else:
        password_name, confirm_name = str(target), confirm
    return {
        "rules": password_rules(),
        "check_url": reverse("users:password_rules"),
        "password_name": password_name,
        "confirm_name": confirm_name,
        "uid": password_name,
    }
