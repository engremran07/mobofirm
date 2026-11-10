"""Live password checklist: one rule per configured validator, computed server-side.

``AUTH_PASSWORD_VALIDATORS`` is the only source of truth. The endpoint runs each
validator separately and answers with per-rule booleans; the password is never
logged or echoed, the POST is CSRF-protected and rate limited, and every page with
a new-password field renders the same rows.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from allauth.account.forms import default_token_generator
from allauth.account.models import EmailAddress
from allauth.account.utils import user_pk_to_url_str
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users import views_password
from apps.users.services.password_rules import (
    MAX_PASSWORD_LENGTH,
    evaluate_password,
    password_rules,
)

pytestmark = pytest.mark.django_db

URL = "/users/password-rules/"
SECRET = "Zx9!UniquePasswordMarker-Qq7"
TWELVE = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def _results(password, values=None, account=None):
    return {r.id: r for r in evaluate_password(password, values, account)}


def _member(**extra):
    user = get_user_model().objects.create_user(
        username="pwuser",
        email="pwuser@example.test",
        password="Old-pass-84213!",
        email_verified_at=timezone.now(),
        profile_completed=True,
        full_name="Pw User",
        country="PK",
        **extra,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _post(client, **data):
    return client.post(URL, data)


class TestRules:
    def test_rules_mirror_the_configured_validators_in_order(self):
        rules = password_rules()

        assert [rule.id for rule in rules] == [
            "similarity",
            "min_length",
            "common",
            "numeric",
        ]
        assert rules[1].label == "At least 8 characters long"

    @override_settings(AUTH_PASSWORD_VALIDATORS=TWELVE)
    def test_the_minimum_length_comes_from_the_validator_options(self):
        rules = password_rules()

        assert [rule.id for rule in rules] == ["min_length", "numeric"]
        assert rules[0].label == "At least 12 characters long"
        results = _results("elevenchars")
        assert results["min_length"].passed is False
        assert _results("twelve-chars")["min_length"].passed is True

    def test_each_rule_reports_independently(self):
        short, numeric = _results("ab1"), _results("123456789")
        common = _results("password")
        good = _results(SECRET)

        assert not short["min_length"].passed and short["numeric"].passed
        assert numeric["min_length"].passed and not numeric["numeric"].passed
        assert not common["common"].passed and common["min_length"].passed
        assert all(result.passed for result in good.values())

    def test_similarity_uses_live_form_values(self):
        values = {"email": "jane.doe@example.test", "username": "janedoe"}

        close = _results("janedoe2024", values)
        far = _results(SECRET, values)

        assert close["similarity"].passed is False and close["similarity"].checked
        assert far["similarity"].passed is True and far["similarity"].checked

    def test_similarity_is_unchecked_without_anything_to_compare(self):
        result = _results(SECRET)["similarity"]

        assert (result.passed, result.checked) == (True, False)

    def test_the_signed_in_account_counts_for_similarity(self):
        user = _member()

        assert _results("pwuser1234", {}, user)["similarity"].passed is False


class TestEndpoint:
    def test_anonymous_visitors_get_per_rule_results(self, client):
        response = _post(client, password="12345678", email="a@example.test")

        body = response.json()
        assert response.status_code == 200
        assert body["ok"] is True and body["valid"] is False
        by_id = {rule["id"]: rule for rule in body["rules"]}
        assert by_id["numeric"]["passed"] is False
        assert by_id["min_length"]["passed"] is True
        assert by_id["similarity"]["checked"] is True

    def test_a_strong_password_is_valid(self, client):
        body = _post(client, password=SECRET).json()

        assert body["valid"] is True
        assert all(rule["passed"] for rule in body["rules"])

    def test_the_password_is_never_echoed_cached_or_logged(self, client, caplog):
        caplog.set_level(logging.DEBUG)

        response = _post(client, password=SECRET)

        assert SECRET not in response.content.decode()
        assert "no-store" in response["Cache-Control"]
        assert SECRET not in caplog.text

    def test_csrf_is_enforced(self):
        strict = Client(enforce_csrf_checks=True)

        assert strict.post(URL, {"password": SECRET}).status_code == 403

    def test_a_valid_csrf_token_is_accepted(self):
        strict = Client(enforce_csrf_checks=True)
        page = strict.get(reverse("users:account_signup"))
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()
        )
        assert token is not None

        response = strict.post(
            URL, {"password": SECRET}, HTTP_X_CSRFTOKEN=token.group(1)
        )

        assert response.status_code == 200

    def test_get_is_not_allowed(self, client):
        assert client.get(URL).status_code == 405

    def test_oversized_passwords_are_refused(self, client):
        response = _post(client, password="a" * (MAX_PASSWORD_LENGTH + 1))

        assert response.status_code == 400
        assert response.json()["error"] == "too_long"

    def test_scripted_abuse_is_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr(views_password, "RULE_CHECKS_PER_MINUTE", 2)

        codes = [_post(client, password=SECRET).status_code for _ in range(4)]

        assert codes == [200, 200, 429, 429]

    def test_signed_in_incomplete_profiles_can_use_it(self, client):
        user = _member()
        get_user_model().objects.filter(pk=user.pk).update(profile_completed=False)
        client.force_login(user)

        response = _post(client, password="pwuser1234")

        assert response.status_code == 200
        similarity = next(
            r for r in response.json()["rules"] if r["id"] == "similarity"
        )
        assert similarity["passed"] is False

    def test_the_response_is_json_only(self, client):
        response = _post(client, password=SECRET)

        assert response["Content-Type"].startswith("application/json")
        assert set(json.loads(response.content)) == {"ok", "valid", "rules"}


def _assert_checklist(html: str, *, confirm: bool = True):
    assert 'x-data="passwordChecklist"' in html
    assert f'data-check-url="{URL}"' in html
    for rule in password_rules():
        assert f'data-rule="{rule.id}"' in html
        assert rule.label in html
    assert ('data-rule="match"' in html) is confirm
    assert 'aria-live="polite"' in html


class TestPages:
    def test_signup_renders_the_checklist_with_a_match_row(self, client):
        html = client.get(reverse("users:account_signup")).content.decode()

        _assert_checklist(html)
        assert 'data-password-name="password1"' in html
        assert 'data-confirm-name="password2"' in html

    @override_settings(AUTH_PASSWORD_VALIDATORS=TWELVE)
    def test_pages_render_the_minimum_length_from_the_validator(self, client):
        html = client.get(reverse("users:account_signup")).content.decode()

        assert "At least 12 characters long" in html
        assert "At least 8 characters long" not in html

    def test_login_has_no_checklist(self, client):
        html = client.get(reverse("users:account_login")).content.decode()

        assert "passwordChecklist" not in html

    def test_tell_us_about_you_renders_the_checklist(self, client):
        user = _member()
        get_user_model().objects.filter(pk=user.pk).update(profile_completed=False)
        client.force_login(user)

        html = client.get(reverse("users:tell_us_about_you")).content.decode()

        _assert_checklist(html)

    def test_profile_change_password_renders_the_checklist(self, client):
        client.force_login(_member())

        html = client.get(reverse("users:profile")).content.decode()

        _assert_checklist(html)
        assert 'data-password-name="new_password1"' in html
        assert 'data-confirm-name="new_password2"' in html

    def test_allauth_change_password_renders_the_checklist(self, client):
        client.force_login(_member())

        response = client.get(reverse("account_change_password"))

        assert response.status_code == 200
        _assert_checklist(response.content.decode())

    def test_password_reset_from_key_renders_the_checklist(self, client):
        user = _member()
        key = default_token_generator.make_token(user)
        start = f"/accounts/password/reset/key/{user_pk_to_url_str(user)}-{key}/"

        response = client.get(start, follow=True)

        assert response.status_code == 200
        _assert_checklist(response.content.decode())
