"""Social sign-in: the buttons are always listed, and one helper decides "configured".

* ``apps.users.services.social_providers`` is the single source of configured-state
  (admin ``SocialApp`` rows and the ``GOOGLE_OAUTH_*`` settings app).
* Login and sign-up always show "Continue with Google". A configured provider posts to
  its OAuth start URL; an unconfigured one opens an app-owned dialog and submits nothing.
* The SocialApp admin pre-selects the current site and explains how to get the keys.
"""

from __future__ import annotations

import re

import pytest
from allauth.socialaccount.models import SocialApp
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.users.adapters import CustomSocialAccountAdapter
from apps.users.admin import EnhancedSocialAppAdmin
from apps.users.services.social_providers import (
    credentials_complete,
    provider_configured,
    social_provider_states,
)

pytestmark = pytest.mark.django_db

PAGES = ("users:account_login", "users:account_signup")
ENV_SETTINGS = {
    "google": {"APP": {"client_id": "env-client-id-123", "secret": "env-secret-456"}}
}


def _request(path: str = "/users/login/"):
    return RequestFactory().get(path)


def _google_app(*, attach_site: bool = True, **overrides) -> SocialApp:
    values = {
        "provider": "google",
        "name": "Google",
        "client_id": "1234567890-real.apps.googleusercontent.com",
        "secret": "real-secret-value",
    }
    values.update(overrides)
    app = SocialApp.objects.create(**values)
    if attach_site:
        app.sites.add(Site.objects.get_current())
    return app


@pytest.fixture(autouse=True)
def no_env_provider():
    """Start every test with no environment-configured provider."""
    with override_settings(SOCIALACCOUNT_PROVIDERS={}):
        yield


class TestConfiguredState:
    def test_nothing_configured_lists_google_as_unconfigured(self):
        states = social_provider_states(_request())

        assert [(state.id, state.configured) for state in states] == [("google", False)]
        assert states[0].label == "Continue with Google"
        assert states[0].unavailable_message == (
            "Google sign-in is not set up yet by the site admin."
        )
        assert states[0].login_url == "/accounts/google/login/?process=login"

    def test_environment_keys_configure_the_provider(self):
        with override_settings(SOCIALACCOUNT_PROVIDERS=ENV_SETTINGS):
            assert provider_configured(_request(), "google") is True

    def test_a_saved_app_on_this_site_configures_the_provider(self):
        _google_app()

        assert provider_configured(_request(), "google") is True

    def test_a_saved_app_without_a_site_does_not(self):
        _google_app(attach_site=False)

        assert provider_configured(_request(), "google") is False

    @pytest.mark.parametrize(
        ("client_id", "secret"),
        [
            ("", "secret"),
            ("client", ""),
            ("your-client-id-here", "secret"),
            ("client", "PLACEHOLDER"),
            ("changeme", "changeme"),
        ],
    )
    def test_blank_or_placeholder_keys_do_not(self, client_id, secret):
        _google_app(client_id=client_id, secret=secret)

        assert provider_configured(_request(), "google") is False
        assert credentials_complete(client_id, secret) is False

    def test_a_safe_next_is_carried_and_a_foreign_one_is_not(self):
        safe = social_provider_states(_request("/users/login/?next=/marketplace/sell/"))
        foreign = social_provider_states(
            _request("/users/login/?next=https://evil.example/")
        )

        assert safe[0].login_url.endswith("process=login&next=%2Fmarketplace%2Fsell%2F")
        assert "next" not in foreign[0].login_url


@pytest.mark.parametrize("name", PAGES)
class TestPages:
    def test_unconfigured_provider_button_opens_the_app_dialog(self, client, name):
        html = client.get(reverse(name)).content.decode()

        assert "Continue with Google" in html
        button = re.search(r'<button[^>]*data-social-provider="google"[^>]*>', html)
        assert button is not None
        assert 'type="button"' in button.group(0)
        assert 'data-dialog-open="social-unavailable-google"' in button.group(0)
        assert 'id="social-unavailable-google"' in html
        assert "Google sign-in is not set up yet by the site admin." in html
        assert "data-app-dialog" in html
        # Nothing can be submitted to the provider.
        assert 'action="/accounts/google/login/' not in html

    def test_configured_provider_button_posts_with_csrf(self, client, name):
        _google_app()

        html = client.get(reverse(name)).content.decode()

        form = re.search(
            r'<form[^>]*data-social-provider="google"[^>]*>(.*?)</form>',
            html,
            re.DOTALL,
        )
        assert form is not None
        assert 'method="post"' in form.group(0)
        assert 'action="/accounts/google/login/?process=login"' in form.group(0)
        assert "csrfmiddlewaretoken" in form.group(1)
        assert "Continue with Google" in form.group(1)
        assert "social-unavailable-google" not in html

    def test_dialog_has_no_native_prompts_or_inline_handlers(self, client, name):
        html = client.get(reverse(name)).content.decode()

        assert not re.search(r"\bon(click|submit)=", html)
        assert "alert(" not in html and "confirm(" not in html


class TestOAuthStart:
    def test_a_configured_provider_continues_into_oauth(self, client):
        _google_app()

        response = client.post("/accounts/google/login/?process=login")

        assert response.status_code == 302
        assert response["Location"].startswith("https://accounts.google.com/")
        assert (
            "client_id=1234567890-real.apps.googleusercontent.com"
            in (response["Location"])
        )

    def test_an_unconfigured_provider_sends_the_visitor_back_with_a_message(
        self, client
    ):
        response = client.post("/accounts/google/login/?process=login")

        assert response.status_code == 302
        assert response["Location"] == reverse("users:account_login")
        texts = [str(message) for message in get_messages(response.wsgi_request)]
        assert any("not configured" in text for text in texts)

    def test_admin_and_environment_keys_together_prefer_the_admin_row(self):
        saved = _google_app()
        request = _request()
        with override_settings(SOCIALACCOUNT_PROVIDERS=ENV_SETTINGS):
            app = CustomSocialAccountAdapter().get_app(request, "google")

        assert app.pk == saved.pk


@pytest.fixture(name="admin_client")
def admin_client_fixture(client):
    root = get_user_model().objects.create_superuser(
        username="root", email="root@example.test", password="Root-pass-48213!"
    )
    client.force_login(root)
    return client


class TestAdminConfiguration:
    def test_the_add_form_preselects_the_site_and_explains_the_keys(self, admin_client):
        response = admin_client.get(reverse("admin:socialaccount_socialapp_add"))

        html = response.content.decode()
        assert response.status_code == 200
        assert "https://console.cloud.google.com/apis/credentials" in html
        assert "/accounts/&lt;provider&gt;/login/callback/" in html
        initial = response.context["adminform"].form.initial
        assert initial["sites"] == [Site.objects.get_current().pk]

    def test_saving_without_a_site_attaches_the_current_one(self, admin_client):
        response = admin_client.post(
            reverse("admin:socialaccount_socialapp_add"),
            {
                "provider": "google",
                "name": "Google",
                "client_id": "1234567890-abc.apps.googleusercontent.com",
                "secret": "a-real-secret",
                "key": "",
                "settings": "{}",
            },
        )

        assert response.status_code == 302
        app = SocialApp.objects.get(provider="google")
        assert list(app.sites.all()) == [Site.objects.get_current()]
        assert provider_configured(_request(), "google") is True

    def test_status_reflects_real_versus_placeholder_keys(self):
        admin_model = EnhancedSocialAppAdmin(SocialApp, admin.site)

        assert admin_model.status_display(_google_app()) == "Configured"
        assert admin_model.status_display(_google_app(client_id="your-id")) == (
            "Placeholder"
        )
