"""Staff console for the link shortener: permissions, write-only key, health button."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.firmwares import shortener, shortener_providers
from apps.firmwares.models import Brand
from apps.firmwares.shortener_constants import (
    BREAKER_THRESHOLD,
    ProviderKind,
    ShortenerMode,
)
from apps.firmwares.shortener_models import ShortenerOutcome, ShortenerSettings
from apps.seo.fetcher import FetchResult

pytestmark = pytest.mark.django_db

SECRET = "sk-live-TOPSECRET-9876"
DIALOG = {"_form_submitted": "on"}
ENDPOINT = "https://shrinkme.test/api?api={key}&url={url}&format=text"


class OneAnswer:
    def __init__(self, text: str = "https://shrinkme.test/xyz", status: int = 200):
        self.calls = 0
        self.text, self.status = text, status

    def fetch(self, url, **_kwargs):
        self.calls += 1
        return FetchResult(
            url=url,
            final_url=url,
            status=self.status,
            headers={"content-type": "text/plain"},
            body=self.text.encode(),
        )


@pytest.fixture(autouse=True)
def _guard(monkeypatch):
    def refuse(timeout):
        raise AssertionError("real SafeFetcher built")

    monkeypatch.setattr(shortener_providers, "default_fetcher", refuse)
    monkeypatch.setattr(
        shortener, "absolute_url", lambda path, request=None: f"https://site.test{path}"
    )


@pytest.fixture(name="staff")
def staff_fixture(django_user_model, client):
    user = django_user_model.objects.create_superuser(
        username="short-admin", email="short-admin@example.test"
    )
    client.force_login(user)
    return client


def settings_url() -> str:
    return reverse("admin:firmwares_shortenersettings_change")


def action_url() -> str:
    return reverse(
        "admin:firmwares_shortenersettings_check_provider",
        args=[ShortenerSettings.get_solo().pk],
    )


def form_data(**extra) -> dict[str, str]:
    data = {
        "mode": "off",
        "provider_kind": "isgd",
        "provider_endpoint": "",
        "provider_method": "GET",
        "provider_auth_header": "",
        "provider_auth_prefix": "",
        "provider_body_template": '{"url": "{url}"}',
        "provider_response_path": "",
        "timeout_seconds": "4",
        "new_api_key": "",
    }
    data.update(extra)
    return data


def test_anonymous_visitors_are_sent_to_login(client):
    response = client.get(settings_url())

    assert response.status_code == 302
    assert "login" in response["Location"]


def test_staff_without_the_model_permission_is_refused(django_user_model, client):
    user = django_user_model.objects.create_user(
        username="plain-staff", email="plain@example.test", is_staff=True
    )
    client.force_login(user)

    assert client.get(settings_url()).status_code == 403
    assert client.post(settings_url(), form_data(mode="both")).status_code == 403
    assert client.post(action_url(), DIALOG).status_code == 403
    assert ShortenerSettings.get_solo().mode == ShortenerMode.OFF


def test_a_viewer_can_read_but_not_change_or_test(django_user_model, client):
    user = django_user_model.objects.create_user(
        username="viewer", email="viewer@example.test", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_shortenersettings"))
    client.force_login(user)

    assert client.get(settings_url()).status_code == 200
    assert client.post(settings_url(), form_data(mode="both")).status_code == 403
    assert client.post(action_url(), DIALOG).status_code == 403


def test_the_page_explains_the_modes_and_warns_about_monetized_shorteners(staff):
    html = staff.get(settings_url()).content.decode()

    assert "ad page" in html and "attribution" in html
    assert "Safe Browsing" in html and "pay little" in html
    assert "First-party" in html


def test_saving_the_mode_keeps_the_runtime_state(staff):
    row = ShortenerSettings.get_solo()
    row.breaker_failures = 3
    row.cache_epoch = 4
    row.save()

    response = staff.post(settings_url(), form_data(mode="first_party"))

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.mode == "first_party"
    assert (row.breaker_failures, row.cache_epoch) == (3, 4)


def test_a_new_api_key_is_encrypted_and_never_echoed(staff):
    data = form_data(
        mode="third_party",
        provider_kind="query_api",
        provider_endpoint=ENDPOINT,
        new_api_key=SECRET,
    )

    saved = staff.post(settings_url(), data)

    assert saved.status_code == 302
    row = ShortenerSettings.get_solo()
    assert row.has_api_key and SECRET not in row.provider_api_key_encrypted
    assert row.get_api_key() == SECRET
    page = staff.get(settings_url())
    assert SECRET not in page.content.decode()
    assert "A key is stored" in page.content.decode()
    again = staff.post(
        settings_url(), {**data, "new_api_key": "", "timeout_seconds": "5"}
    )
    assert again.status_code == 302
    assert ShortenerSettings.get_solo().get_api_key() == SECRET


def test_a_rejected_form_does_not_echo_the_key_either(staff):
    response = staff.post(
        settings_url(),
        form_data(
            mode="third_party",
            provider_kind="query_api",
            provider_endpoint="http://x.test/?u={url}",
            new_api_key=SECRET,
        ),
    )

    assert response.status_code == 200
    assert SECRET not in response.content.decode()
    assert (
        not ShortenerSettings.get_solo().has_api_key
        or ShortenerSettings.get_solo().mode == "off"
    )


def test_the_key_can_be_removed(staff):
    row = ShortenerSettings.get_solo()
    row.set_api_key(SECRET)
    row.save()

    staff.post(settings_url(), form_data(clear_api_key="on"))

    assert not ShortenerSettings.get_solo().has_api_key


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        (
            {
                "mode": "third_party",
                "provider_kind": "query_api",
                "provider_endpoint": "http://x.test/?k={key}&u={url}",
                "new_api_key": "k",
            },
            "provider_endpoint",
        ),
        (
            {
                "mode": "third_party",
                "provider_kind": "query_api",
                "provider_endpoint": ENDPOINT,
            },
            "new_api_key",
        ),
        (
            {
                "mode": "both",
                "provider_kind": "json_api",
                "provider_endpoint": "https://x.test/s",
                "provider_method": "POST",
                "provider_body_template": "[1]",
            },
            "provider_body_template",
        ),
        ({"provider_response_path": "a b;c"}, "provider_response_path"),
        ({"timeout_seconds": "99"}, "timeout_seconds"),
    ],
)
def test_invalid_provider_settings_are_rejected_and_nothing_is_saved(
    staff, extra, field
):
    response = staff.post(settings_url(), form_data(**extra))

    assert response.status_code == 200
    assert field in response.context["adminform"].form.errors
    assert ShortenerSettings.get_solo().mode == ShortenerMode.OFF


def test_changing_the_provider_empties_the_cache_and_resets_the_circuit(staff):
    row = ShortenerSettings.get_solo()
    row.breaker_failures = BREAKER_THRESHOLD
    row.breaker_open_until = timezone.now() + timedelta(hours=1)
    row.save()

    staff.post(settings_url(), form_data(mode="third_party", provider_kind="tinyurl"))

    row.refresh_from_db()
    assert row.cache_epoch == 1
    assert (row.breaker_failures, row.breaker_open_until) == (0, None)


def test_the_health_dialog_opens_without_calling_the_provider(staff, monkeypatch):
    fake = OneAnswer()
    monkeypatch.setattr(shortener_providers, "default_fetcher", lambda timeout: fake)

    response = staff.get(action_url(), headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert fake.calls == 0


def test_the_health_check_runs_on_post_and_records_the_result(staff, monkeypatch):
    fake = OneAnswer("https://shrinkme.test/probe")
    monkeypatch.setattr(shortener_providers, "default_fetcher", lambda timeout: fake)
    row = ShortenerSettings.get_solo()
    row.mode, row.provider_kind = "third_party", ProviderKind.ISGD
    row.save()

    done = staff.post(action_url(), DIALOG, headers={"HX-Request": "true"})

    assert "HX-Redirect" in done.headers
    assert fake.calls == 1
    row.refresh_from_db()
    assert row.last_check_ok is True
    assert ShortenerOutcome.objects.get().stage == "health_check"
    follow = staff.get(settings_url())
    assert "OK" in follow.content.decode()


def test_the_health_check_enforces_csrf(django_user_model):
    user = django_user_model.objects.create_superuser(
        username="csrf-admin", email="csrf-admin@example.test"
    )
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)

    assert strict.post(action_url(), DIALOG).status_code == 403
    assert ShortenerOutcome.objects.count() == 0


def test_the_brand_page_offers_the_per_brand_choice(staff):
    brand = Brand.objects.create(name="Pick", slug="pick")

    response = staff.get(reverse("admin:firmwares_brand_change", args=[brand.pk]))

    assert response.status_code == 200
    assert b"shortener_mode" in response.content


def test_the_audit_pages_are_read_only(staff):
    for name in ("downloadshortlink", "thirdpartyshortlink", "shorteneroutcome"):
        assert (
            staff.get(reverse(f"admin:firmwares_{name}_changelist")).status_code == 200
        )
        assert staff.get(reverse(f"admin:firmwares_{name}_add")).status_code == 403


def test_the_override_page_lists_and_adds(staff):
    assert (
        staff.get(
            reverse("admin:firmwares_firmwareshorteneroverride_changelist")
        ).status_code
        == 200
    )
    assert (
        staff.get(reverse("admin:firmwares_firmwareshorteneroverride_add")).status_code
        == 200
    )
