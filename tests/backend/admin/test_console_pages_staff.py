"""Every custom staff page renders in the console shell and enforces its permission."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.distribution.models import Channel
from apps.pages.models import Page
from apps.seo.models import Issue
from tests.backend.admin.admin_model_factory import build

PageUrl = Callable[[], str]


def _channel_preview() -> str:
    channel = build(Channel)
    assert isinstance(channel, Channel)
    return reverse("admin:distribution_channel_preview", args=[channel.pk])


def _page_preview() -> str:
    page = build(Page)
    assert isinstance(page, Page)
    return reverse("admin:pages_page_preview_page", args=[page.pk])


def _named(name: str) -> PageUrl:
    return lambda: reverse(name)


def _seo_issue_detail() -> str:
    issue = build(Issue)
    assert isinstance(issue, Issue)
    Issue.objects.filter(pk=issue.pk).update(url="/")
    return f"{reverse('seo:fix')}?issue={issue.pk}"


STAFF_PAGES: dict[str, PageUrl] = {
    "consent-stats": _named("admin:consent_consentdecision_stats"),
    "notification-dashboard": _named("admin:users_notification_dashboard"),
    "distribution-dashboard": _named("admin:distribution_channel_dashboard"),
    "distribution-calendar": _named("admin:distribution_channel_calendar"),
    "distribution-review": _named("admin:distribution_channel_review"),
    "distribution-analytics": _named("admin:distribution_channel_analytics"),
    "distribution-platforms": _named("admin:distribution_channel_platforms"),
    "distribution-preview": _channel_preview,
    "page-preview": _page_preview,
    "ads-dashboard": _named("ads:dashboard"),
    "ads-placements": _named("ads:placement_manager"),
    "ads-suggestions": _named("ads:discoveries"),
    "seo-dashboard": _named("seo:dashboard"),
    "seo-issues": _named("seo:issues"),
    "seo-issue-detail": _seo_issue_detail,
    "seo-inventory": _named("seo:inventory"),
    "seo-inspect": _named("seo:inspect"),
    "seo-links": _named("seo:links"),
    "seo-ledger": _named("seo:ledger"),
    "seo-jobs": _named("seo:jobs"),
    "seo-guides": _named("seo:guides"),
    "seo-settings": _named("seo:settings"),
    "tags-manage": _named("tags:manage"),
    "tags-duplicates": _named("tags:duplicates_review"),
    "tags-keywords": _named("tags:keyword_review"),
    "comments-moderation": _named("comments:moderation_queue"),
    "devices-events": _named("devices:device_events"),
    "storage-status": _named("storage:status"),
}


@pytest.fixture(name="superuser_client")
def superuser_client_fixture(django_user_model) -> Client:
    user = django_user_model.objects.create_superuser(
        username="console-pages-root", email="console-pages-root@example.test"
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture(name="bare_staff_client")
def bare_staff_client_fixture(django_user_model) -> Client:
    user = django_user_model.objects.create_user(
        username="console-pages-bare",
        email="console-pages-bare@example.test",
        is_staff=True,
    )
    client = Client()
    client.force_login(user)
    return client


def _html(response: Any) -> str:
    return response.content.decode()


@pytest.mark.parametrize("page", STAFF_PAGES)
def test_staff_page_renders_in_the_console_shell(superuser_client, page):
    response = superuser_client.get(STAFF_PAGES[page]())

    assert response.status_code == 200
    html = _html(response)
    assert "console-staff-page" in html
    assert "<h1" in html
    assert "mf-ui-" not in html


@pytest.mark.parametrize(
    "page",
    [page for page in STAFF_PAGES if page not in {"seo-issues", "seo-issue-detail"}],
)
def test_staff_page_over_htmx_is_the_content_only_fragment(superuser_client, page):
    response = superuser_client.get(STAFF_PAGES[page](), HTTP_HX_REQUEST="true")

    html = _html(response)
    assert response.status_code == 200
    assert "console-staff-page" in html and "<title>" in html
    assert "<aside" not in html and "<html" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("page", STAFF_PAGES)
def test_staff_page_sends_anonymous_visitors_to_sign_in(page):
    response = Client().get(STAFF_PAGES[page]())

    assert response.status_code == 302
    assert "login" in response["Location"]


PERMISSION_GATED = [
    page
    for page in STAFF_PAGES
    if not page.startswith(("ads-", "seo-", "comments-", "storage-"))
]
# Read-only for any staff member by design; every write behind them needs its own model
# permission (see test_staff_page_writes_are_refused_without_permission).
OPEN_TO_STAFF = [page for page in STAFF_PAGES if page not in PERMISSION_GATED]
WRITE_ENDPOINTS = {
    "seo-run-scan": "seo:run_scan",
    "seo-process-jobs": "seo:process_jobs",
    "ads-run-scan": "ads:run_scan",
}


@pytest.mark.parametrize("page", PERMISSION_GATED)
def test_staff_page_refuses_staff_without_the_model_permission(bare_staff_client, page):
    response = bare_staff_client.get(STAFF_PAGES[page]())

    assert response.status_code == 403


@pytest.mark.parametrize("page", OPEN_TO_STAFF)
def test_read_only_staff_page_never_offers_a_write_to_staff_without_permission(
    bare_staff_client, page
):
    response = bare_staff_client.get(STAFF_PAGES[page]())

    assert response.status_code in {200, 403}
    if response.status_code == 200:
        html = _html(response)
        for write_label in ("Run scan now", "Approve", "Generate", "Apply fix"):
            assert not re.search(rf"<(?:button|a)\b[^>]*>\s*{write_label}\b", html)


@pytest.mark.parametrize("endpoint", WRITE_ENDPOINTS)
def test_staff_page_writes_are_refused_without_permission(bare_staff_client, endpoint):
    response = bare_staff_client.post(reverse(WRITE_ENDPOINTS[endpoint]))

    assert response.status_code == 403
    assert response.status_code == 403


def test_seo_settings_are_read_only_without_the_change_permission(bare_staff_client):
    response = bare_staff_client.get(reverse("seo:settings"))

    html = _html(response)
    assert response.status_code == 200
    assert "<fieldset disabled>" in html
    assert "Save settings" not in html and "need the" in html
    assert 'name="csrfmiddlewaretoken"' in html
