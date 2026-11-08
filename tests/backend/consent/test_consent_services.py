"""Consent contract: ``has_consent``, the cookie, audit records and retention."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.consent import services
from apps.consent.constants import ADVERTISING, ANALYTICS, NECESSARY
from apps.consent.models import ConsentCategory, ConsentDecision, ConsentPolicy
from apps.core.models import AppRegistry

pytestmark = pytest.mark.django_db


def request_with(cookie: str | None = None):
    request = RequestFactory().get("/")
    if cookie is not None:
        request.COOKIES[services.cookie_name()] = cookie
    return request


def cookie_for(*granted: str, version: int | None = None) -> str:
    version = version or services.active_policy().version
    return services.encode_cookie(version, granted, uuid.uuid4())


def test_defaults_are_created_lazily_and_idempotently():
    ConsentPolicy.objects.all().delete()
    ConsentCategory.objects.all().delete()
    cache.clear()
    view = services.active_policy()
    assert view.version == 1
    assert {c.slug for c in view.categories} == {NECESSARY, ANALYTICS, ADVERTISING}
    services.ensure_defaults()
    assert ConsentPolicy.objects.count() == 1
    assert ConsentCategory.objects.count() == 3


def test_optional_categories_are_opt_in_and_necessary_is_always_allowed():
    request = request_with()
    assert services.has_consent(request, NECESSARY) is True
    assert services.has_consent(request, ANALYTICS) is False
    assert services.has_consent(request, ADVERTISING) is False
    assert services.has_consent(request, "unknown") is False
    assert services.needs_decision(request) is True


def test_cookie_grants_only_what_it_lists():
    request = request_with(cookie_for(ANALYTICS))
    assert services.has_consent(request, ANALYTICS) is True
    assert services.has_consent(request, ADVERTISING) is False
    assert services.needs_decision(request) is False


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "garbage",
        "1:analytics",
        "x:analytics:" + "a" * 32,
        "1:Analytics:" + "a" * 32,
    ],
)
def test_malformed_cookies_count_as_no_decision(raw):
    request = request_with(raw)
    assert services.has_consent(request, ANALYTICS) is False
    assert services.needs_decision(request) is True


def test_a_cookie_from_an_older_policy_version_asks_again():
    old = cookie_for(ANALYTICS, ADVERTISING)
    new_policy = services.create_draft_policy()
    services.publish_policy(new_policy)
    request = request_with(old)
    assert services.has_consent(request, ANALYTICS) is False
    assert services.needs_decision(request) is True


def test_cookie_round_trip():
    subject = uuid.uuid4()
    raw = services.encode_cookie(3, {"analytics", "advertising"}, subject)
    assert services.parse_cookie(raw) == (3, {"analytics", "advertising"}, subject)
    assert services.parse_cookie(services.encode_cookie(3, set(), subject)) == (
        3,
        frozenset(),
        subject,
    )


def test_switching_the_app_off_denies_optional_categories():
    cookie = cookie_for(ANALYTICS)
    registry = AppRegistry.get_solo()
    registry.consent_enabled = False
    registry.save()
    request = request_with(cookie)
    assert services.has_consent(request, ANALYTICS) is False
    assert services.has_consent(request, NECESSARY) is True
    assert services.needs_decision(request) is False


def test_only_one_policy_can_be_active():
    services.ensure_defaults()
    with pytest.raises(IntegrityError), transaction.atomic():
        ConsentPolicy.objects.create(
            version=99, banner_text="x", is_active=True, published_at=timezone.now()
        )


def test_publishing_switches_the_active_version_and_snapshots_categories():
    first = services.ensure_defaults()
    ConsentCategory.objects.filter(slug=ANALYTICS).update(name="Statistics")
    draft = services.create_draft_policy(banner_text="New text")
    assert draft.version == 2 and draft.is_active is False
    services.publish_policy(draft)
    first.refresh_from_db()
    draft.refresh_from_db()
    assert first.is_active is False and draft.is_active is True
    assert any(c["name"] == "Statistics" for c in draft.categories_snapshot)
    assert services.active_policy().banner_text == "New text"


def test_decisions_are_anonymised_and_deduplicated():
    request = RequestFactory().post(
        "/consent/save/", REMOTE_ADDR="203.0.113.7", HTTP_USER_AGENT="TestAgent/1.0"
    )
    from django.http import HttpResponse

    response = HttpResponse()
    services.record_decision(request, response, action="accept_all")
    record = ConsentDecision.objects.get()
    assert record.granted == [ADVERTISING, ANALYTICS]
    assert "203.0.113.7" not in (record.ip_hash, record.user_agent_hash)
    assert len(record.ip_hash) == 64 and record.ip_hash != record.user_agent_hash
    cookie = response.cookies[services.cookie_name()].value

    again = request_with(cookie)
    again.method = "POST"
    services.record_decision(again, HttpResponse(), action="accept_all")
    assert ConsentDecision.objects.count() == 1

    services.record_decision(again, HttpResponse(), action="reject_all")
    assert ConsentDecision.objects.count() == 2
    assert ConsentDecision.objects.filter(subject=record.subject).count() == 2


def test_custom_choice_ignores_unknown_and_required_slugs():
    from django.http import HttpResponse

    response = HttpResponse()
    state = services.record_decision(
        request_with(),
        response,
        action="custom",
        selected=[ANALYTICS, NECESSARY, "bogus"],
    )
    assert state.granted == {ANALYTICS}


def test_unknown_action_is_rejected():
    from django.http import HttpResponse

    with pytest.raises(ValueError):
        services.record_decision(request_with(), HttpResponse(), action="maybe")


def test_withdrawal_expires_tracking_cookies():
    from django.http import HttpResponse

    cookie = cookie_for(ANALYTICS)
    request = request_with(cookie)
    request.COOKIES["_ga"] = "GA1.1.1"
    request.COOKIES["_ga_ABC123"] = "x"
    request.COOKIES["sessionid"] = "keep"
    response = HttpResponse()
    services.record_decision(request, response, action="reject_all")
    assert response.cookies["_ga"]["max-age"] == 0
    assert response.cookies["_ga_ABC123"]["max-age"] == 0
    assert "sessionid" not in response.cookies


def test_stats_count_the_latest_choice_per_visitor():
    from django.http import HttpResponse

    first = HttpResponse()
    services.record_decision(request_with(), first, action="accept_all")
    cookie = first.cookies[services.cookie_name()].value
    services.record_decision(
        request_with(cookie), HttpResponse(), action="custom", selected=[ANALYTICS]
    )
    services.record_decision(request_with(), HttpResponse(), action="reject_all")
    stats = services.consent_stats()
    assert stats["visitors"] == 2
    by_slug = {row["slug"]: row for row in stats["by_category"]}
    assert by_slug[ANALYTICS]["granted"] == 1
    assert by_slug[ADVERTISING]["granted"] == 0


def test_purge_removes_only_records_past_retention():
    policy = services.ensure_defaults()
    old = ConsentDecision.objects.create(
        subject=uuid.uuid4(), policy=policy, action="accept_all", source="banner"
    )
    ConsentDecision.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=1200)
    )
    fresh = ConsentDecision.objects.create(
        subject=uuid.uuid4(), policy=policy, action="reject_all", source="banner"
    )
    assert services.purge_old_records() == 1
    assert list(ConsentDecision.objects.all()) == [fresh]
    assert services.purge_old_records(days=0) == 0


def test_builtin_necessary_category_cannot_be_weakened():
    services.ensure_defaults()
    necessary = ConsentCategory.objects.get(slug=NECESSARY)
    necessary.is_required = False
    necessary.is_active = False
    necessary.save()
    necessary.refresh_from_db()
    assert necessary.is_required is True and necessary.is_active is True
