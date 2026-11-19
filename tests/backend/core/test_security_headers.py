"""Permissions-Policy and Cross-Origin-Resource-Policy, beside the strict CSP."""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from apps.core.middleware.admin_csp import AdminContentSecurityPolicyMiddleware
from apps.core.middleware.security_headers import (
    apply_security_headers,
    permissions_policy_value,
)


def test_policy_serialisation_covers_empty_self_and_origin_allowlists():
    value = permissions_policy_value(
        {"camera": [], "fullscreen": ["self"], "geolocation": ["https://maps.example"]}
    )

    assert value == (
        'camera=(), fullscreen=(self), geolocation=("https://maps.example")'
    )


def test_defaults_disable_unused_features_and_keep_embeds_working():
    response = apply_security_headers(HttpResponse("ok"))

    policy = response["Permissions-Policy"]
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{feature}=()" in policy
    # Features embedded video players rely on stay at the browser default.
    for feature in ("fullscreen", "autoplay", "picture-in-picture", "encrypted-media"):
        assert feature not in policy
    assert response["Cross-Origin-Resource-Policy"] == "same-site"


def test_a_view_that_sets_either_header_keeps_its_value():
    response = HttpResponse("ok")
    response["Permissions-Policy"] = "camera=(self)"
    response["Cross-Origin-Resource-Policy"] = "cross-origin"

    apply_security_headers(response)

    assert response["Permissions-Policy"] == "camera=(self)"
    assert response["Cross-Origin-Resource-Policy"] == "cross-origin"


@override_settings(PERMISSIONS_POLICY={"camera": ["self"]})
def test_the_policy_is_configurable():
    assert apply_security_headers(HttpResponse())["Permissions-Policy"] == (
        "camera=(self)"
    )


@override_settings(PERMISSIONS_POLICY={}, CROSS_ORIGIN_RESOURCE_POLICY="")
def test_both_headers_can_be_switched_off():
    response = apply_security_headers(HttpResponse())

    assert "Permissions-Policy" not in response
    assert "Cross-Origin-Resource-Policy" not in response


@override_settings(CROSS_ORIGIN_RESOURCE_POLICY="typo")
def test_an_invalid_corp_value_is_never_sent():
    assert "Cross-Origin-Resource-Policy" not in apply_security_headers(HttpResponse())


@pytest.mark.django_db
def test_public_pages_carry_both_headers_and_the_csp_is_untouched():
    response = Client().get(reverse("health-live"))

    assert response["Permissions-Policy"].startswith("accelerometer=()")
    assert response["Cross-Origin-Resource-Policy"] == "same-site"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response["Content-Security-Policy"]


@pytest.mark.django_db
def test_console_responses_keep_the_strict_admin_csp_and_get_the_headers(
    django_user_model,
):
    staff = django_user_model.objects.create_superuser(
        username="headers-admin", email="headers-admin@example.com", password="x"
    )
    client = Client()
    client.force_login(staff)

    response = client.get(reverse("admin:index"))

    assert response["Permissions-Policy"].startswith("accelerometer=()")
    assert response["Cross-Origin-Resource-Policy"] == "same-site"
    csp = response["Content-Security-Policy"]
    assert "'unsafe-eval'" not in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_the_middleware_stamps_responses_from_either_branch():
    middleware = AdminContentSecurityPolicyMiddleware(lambda _request: HttpResponse())

    response = middleware(RequestFactory().get("/no-such-route/"))

    assert "Permissions-Policy" in response
    assert response["Cross-Origin-Resource-Policy"] == "same-site"
