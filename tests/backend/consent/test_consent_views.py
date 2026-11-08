"""Banner, privacy centre, footer link, and consent-gated analytics/ads."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.consent import services
from apps.consent.constants import ADVERTISING, ANALYTICS
from apps.consent.models import ConsentDecision
from apps.core.models import AppRegistry
from apps.pages.seed import seed_pages
from apps.site_settings.models import SiteSettings

pytestmark = pytest.mark.django_db

ANALYTICS_LOADER = "site_settings/analytics.js"


@pytest.fixture
def analytics_site():
    row = SiteSettings.get_solo()
    row.google_analytics_id = "G-ABCD1234"
    row.save()


def post_choice(client: Client, action: str, **extra: object):
    return client.post(
        reverse("consent:save"), {"action": action, "source": "banner", **extra}
    )


def test_banner_is_rendered_for_an_undecided_visitor(client):
    html = client.get("/").content.decode()
    assert 'id="consent-banner-title"' in html
    assert 'action="/consent/save/"' in html
    assert "csrfmiddlewaretoken" in html
    assert "Cookie settings" in html


def test_banner_links_the_privacy_policy_only_while_the_page_is_published(client):
    assert 'href="/pages/privacy/"' not in client.get("/").content.decode()

    seed_pages(site_name="Mobofirm", contact_email="legal@example.com")
    cache.clear()
    html = client.get("/").content.decode()
    banner = html.split('id="consent-banner-title"')[1].split("</section>")[0]

    assert 'href="/pages/privacy/"' in banner


def test_banner_disappears_after_a_choice_and_is_remembered(client):
    response = post_choice(client, "reject_all", next="/")
    assert response.status_code == 302
    assert client.cookies[services.cookie_name()]["httponly"]
    assert client.cookies[services.cookie_name()]["samesite"] == "Lax"
    assert 'id="consent-banner-title"' not in client.get("/").content.decode()


def test_banner_is_not_shown_inside_the_privacy_centre(client):
    html = client.get(reverse("consent:privacy_centre")).content.decode()
    assert 'id="consent-banner-title"' not in html
    assert 'id="consent-form-title"' in html


def test_banner_is_hidden_when_the_app_is_switched_off(client):
    registry = AppRegistry.get_solo()
    registry.consent_enabled = False
    registry.save()
    assert 'id="consent-banner-title"' not in client.get("/").content.decode()
    assert (
        "switched off" in client.get(reverse("consent:privacy_centre")).content.decode()
    )


def test_privacy_centre_reflects_and_changes_the_choice(client):
    post_choice(client, "custom", categories=[ANALYTICS])
    page = client.get(reverse("consent:privacy_centre")).content.decode()
    assert "Allowed optional categories" in page
    assert 'value="analytics" id="consent-analytics" checked' in page
    assert 'value="advertising" id="consent-advertising" checked' not in page
    client.post(
        reverse("consent:save"), {"action": "reject_all", "source": "privacy_centre"}
    )
    page = client.get(reverse("consent:privacy_centre")).content.decode()
    assert "All optional categories are off" in page
    assert ConsentDecision.objects.count() == 2


def test_post_requires_csrf_and_get_is_not_allowed():
    strict = Client(enforce_csrf_checks=True)
    assert (
        strict.post(reverse("consent:save"), {"action": "accept_all"}).status_code
        == 403
    )
    assert Client().get(reverse("consent:save")).status_code == 405
    assert ConsentDecision.objects.count() == 0


def test_invalid_choice_is_not_recorded(client):
    response = post_choice(client, "maybe")
    assert response.status_code == 302
    assert response["Location"] == reverse("consent:privacy_centre")
    assert ConsentDecision.objects.count() == 0
    assert services.cookie_name() not in client.cookies


def test_redirect_target_must_be_same_site(client):
    response = post_choice(client, "accept_all", next="https://evil.example/phish")
    assert response["Location"] == reverse("consent:privacy_centre")
    response = post_choice(client, "accept_all", next="//evil.example/")
    assert response["Location"] == reverse("consent:privacy_centre")
    response = post_choice(client, "accept_all", next="/blog/")
    assert response["Location"] == "/blog/"


def test_analytics_loader_needs_the_analytics_category(client, analytics_site):
    assert ANALYTICS_LOADER not in client.get("/").content.decode()
    post_choice(client, "custom", categories=[ADVERTISING])
    assert ANALYTICS_LOADER not in client.get("/").content.decode()
    post_choice(client, "custom", categories=[ANALYTICS])
    assert ANALYTICS_LOADER in client.get("/").content.decode()
    post_choice(client, "reject_all")
    assert ANALYTICS_LOADER not in client.get("/").content.decode()


def test_google_hosts_enter_the_csp_only_after_consent(client, analytics_site):
    def script_src() -> str:
        header = client.get("/")["Content-Security-Policy"]
        return next(p for p in header.split(";") if "script-src" in p)

    assert "googletagmanager" not in script_src()
    post_choice(client, "accept_all")
    assert "googletagmanager" in script_src()


def test_ads_consent_contract_reads_the_consent_cookie(client):
    from django.test import RequestFactory

    from apps.ads.views import _has_ads_consent

    request = RequestFactory().get("/")
    assert _has_ads_consent(request) is False
    post_choice(client, "custom", categories=[ADVERTISING])
    request = RequestFactory().get("/")
    request.COOKIES[services.cookie_name()] = client.cookies[
        services.cookie_name()
    ].value
    assert _has_ads_consent(request) is True


def test_ads_events_are_skipped_without_advertising_consent(client):
    response = client.post(
        reverse("ads:record_event"), {"event_type": "impression", "placement": "x"}
    )
    assert response.json().get("skipped") == "no_consent" or response.status_code == 403


def test_decorator_blocks_until_consent_is_given(client):
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.consent.decorators import consent_required

    @consent_required(ANALYTICS)
    def view(request):
        return HttpResponse("ok")

    request = RequestFactory().get("/")
    assert view(request).status_code == 403
    post_choice(client, "accept_all")
    request = RequestFactory().get("/")
    request.COOKIES[services.cookie_name()] = client.cookies[
        services.cookie_name()
    ].value
    assert view(request).status_code == 200


def test_users_consent_models_are_gone():
    from django.apps import apps

    for name in ("ConsentPolicy", "ConsentDecision", "ConsentEvent"):
        with pytest.raises(LookupError):
            apps.get_model("users", name)
    assert get_user_model()
