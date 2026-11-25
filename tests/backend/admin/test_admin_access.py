"""Admin shell contract: access control, dashboard, sidebar coverage, CSP and branding."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.comments.models import Comment
from apps.core.admin_dashboard import invalidate_metrics
from apps.core.admin_navigation import (
    NAV_GROUPS,
    ModelEntry,
    build_sidebar,
    changelist_url,
    listed_model_labels,
    unlisted_models,
)
from apps.firmwares.models import PendingFirmware
from apps.site_settings.models import SiteSettings
from tests.backend.admin.admin_model_factory import build, registered_admins
from tests.backend.admin.theme_guard import REMOVED_THEME

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", re.IGNORECASE
)


@pytest.fixture(name="superuser")
def superuser_fixture(django_user_model):
    return django_user_model.objects.create_superuser(
        username="shell-admin", email="shell-admin@example.test"
    )


@pytest.fixture(name="staff_without_permissions")
def staff_without_permissions_fixture(django_user_model):
    return django_user_model.objects.create_user(
        username="plain-staff",
        email="plain-staff@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )


def _client_for(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _sidebar_links(response) -> list[str]:
    return [
        item["link"]
        for group in response.context["sidebar_navigation"]
        for item in group["items"]
    ]


# -- access control ---------------------------------------------------------------
@pytest.mark.django_db
def test_anonymous_visitors_are_sent_to_the_project_login():
    response = Client().get("/admin/", follow=True)

    final_url, status = response.redirect_chain[-1]
    assert status == 302
    assert final_url.startswith(reverse("users:account_login"))
    assert "next=%2Fadmin%2F" in final_url


@pytest.mark.django_db
def test_signed_in_non_staff_users_get_403_not_a_redirect_loop():
    user = get_user_model().objects.create_user(
        username="member", email="member@example.test"
    )
    client = _client_for(user)

    response = client.get("/admin/", follow=True)

    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_without_model_permissions_see_an_empty_console(
    staff_without_permissions,
):
    client = _client_for(staff_without_permissions)

    dashboard = client.get(reverse("admin:index"))
    assert dashboard.status_code == 200
    slugs = [widget["slug"] for widget in dashboard.context["widgets"]]
    assert "activity" not in slugs
    assert (
        client.get(
            reverse("admin:console_dashboard_widget", args=["activity"])
        ).status_code
        == 404
    )
    invalidate_metrics()
    attention = client.get(
        reverse("admin:console_dashboard_widget", args=["attention"])
    )
    assert attention.status_code == 200
    assert b'id="kpi-' not in attention.content
    links = _sidebar_links(dashboard)
    assert reverse("admin:index") in links
    assert not [
        link for link in links if link.startswith("/admin/") and link != "/admin/"
    ]

    for model in registered_admins():
        url = reverse(
            f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
        )
        assert client.get(url).status_code == 403, model._meta.label


# -- dashboard and sidebar --------------------------------------------------------
@pytest.mark.django_db
def test_dashboard_lists_the_queues_that_hold_work_for_a_superuser(superuser):
    build(PendingFirmware)
    comment = build(Comment)
    assert isinstance(comment, Comment)
    Comment.objects.filter(pk=comment.pk).update(status=Comment.Status.PENDING)
    invalidate_metrics()
    client = _client_for(superuser)

    index = client.get(reverse("admin:index"))
    assert index.status_code == 200
    assert "attention" in [widget["slug"] for widget in index.context["widgets"]]
    # Each panel is a lazily loaded fragment of its own.
    assert b'hx-get="/admin/console/widgets/attention/"' in index.content

    fragment = client.get(
        reverse("admin:console_dashboard_widget", args=["attention"]),
        headers={"HX-Request": "true"},
    )
    assert fragment.status_code == 200
    assert b"<html" not in fragment.content.lower()
    assert b'id="kpi-pending_firmware"' in fragment.content
    assert b'id="kpi-pending_comments"' in fragment.content
    assert fragment.context["queues"]


def test_every_registered_model_is_listed_in_the_sidebar():
    assert unlisted_models() == []


def test_sidebar_only_lists_models_that_are_registered():
    registered = {model._meta.label_lower for model in registered_admins()}
    assert listed_model_labels() <= registered


@pytest.mark.django_db
def test_every_sidebar_link_resolves_for_a_superuser(superuser):
    client = _client_for(superuser)
    response = client.get(reverse("admin:index"))
    groups = response.context["sidebar_navigation"]
    assert not [group for group in groups if group["title"] == "Other"]

    links = _sidebar_links(response)
    expected = {
        changelist_url(entry.label)
        for group in NAV_GROUPS
        for entry in group.entries
        if isinstance(entry, ModelEntry)
    }
    assert expected <= set(links), sorted(expected - set(links), key=str)
    for link in links:
        assert client.get(link).status_code in {200, 302}, link


@pytest.mark.django_db
def test_sidebar_highlights_exactly_one_entry_per_page(superuser):
    client = _client_for(superuser)
    for path in (
        reverse("admin:index"),
        reverse("admin:users_notification_changelist"),
        reverse("admin:users_notification_dashboard"),
        reverse("admin:distribution_channel_dashboard"),
        reverse("admin:consent_consentdecision_stats"),
    ):
        response = client.get(path)
        active = [
            item["link"]
            for group in response.context["sidebar_navigation"]
            for item in group["items"]
            if item.get("active")
        ]
        assert active == [path], (path, active)


@pytest.mark.django_db
def test_custom_admin_pages_keep_the_sidebar_branding(superuser):
    client = _client_for(superuser)
    for path in (
        reverse("admin:users_notification_dashboard"),
        reverse("admin:distribution_channel_dashboard"),
        reverse("admin:consent_consentdecision_stats"),
    ):
        body = client.get(path).content.decode()
        before_main = body.split("<main")[0]
        assert "<nav" in before_main, path
        assert "Mobofirm" in before_main, path


@pytest.mark.django_db
def test_sidebar_hides_models_the_user_cannot_view(staff_without_permissions):
    request = (
        _client_for(staff_without_permissions).get(reverse("admin:index")).wsgi_request
    )

    titles = [
        item["title"] for group in build_sidebar(request) for item in group["items"]
    ]

    assert "Blog posts" not in titles
    assert "Videos" not in titles
    assert "Media library" not in titles
    assert "Dashboard" in titles


# -- CSP --------------------------------------------------------------------------
INLINE_HANDLER = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE)


def _directive(policy: str, name: str) -> str:
    for part in policy.split(";"):
        tokens = part.strip().split()
        if tokens and tokens[0] == name:
            return " ".join(tokens[1:])
    return ""


@pytest.mark.django_db
def test_admin_csp_is_strict_and_distinct_from_the_public_policy(superuser):
    client = _client_for(superuser)
    public = client.get("/").headers["Content-Security-Policy"]
    response = client.get(reverse("admin:index"))
    policy = response.headers["Content-Security-Policy"]
    scripts = _directive(policy, "script-src")

    assert "'unsafe-eval'" not in public
    assert "'unsafe-eval'" not in policy
    assert "'unsafe-inline'" not in scripts
    assert "'self'" in scripts
    assert "'unsafe-inline'" not in _directive(policy, "style-src")
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "http:" not in policy
    assert " * " not in f" {policy} "


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name", ["index", "blog_post_changelist", "blog_post_add", "users_customuser_add"]
)
def test_admin_pages_have_no_inline_script_or_event_handler(superuser, name):
    response = _client_for(superuser).get(reverse(f"admin:{name}"))
    html = response.content.decode()

    assert response.status_code == 200
    assert not INLINE_SCRIPT.findall(html), INLINE_SCRIPT.findall(html)
    assert not INLINE_HANDLER.findall(html), INLINE_HANDLER.findall(html)
    assert "javascript:" not in html.lower()


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["seo:dashboard", "ads:dashboard", "tags:manage"])
def test_staff_pages_get_the_same_strict_policy_as_the_admin(superuser, url_name):
    client = _client_for(superuser)
    admin_policy = client.get(reverse("admin:index")).headers["Content-Security-Policy"]
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "'unsafe-eval'" not in policy, url_name
    assert _directive(policy, "script-src") == _directive(admin_policy, "script-src")
    assert not INLINE_SCRIPT.findall(response.content.decode()), url_name


@pytest.mark.django_db
def test_public_pages_keep_the_strict_policy_after_visiting_the_admin(superuser):
    client = _client_for(superuser)
    client.get(reverse("admin:index"))

    assert "'unsafe-eval'" not in client.get("/").headers["Content-Security-Policy"]


# -- branding from the site settings accessor -----------------------------------------
@pytest.mark.django_db
def test_admin_header_follows_the_runtime_site_settings(superuser):
    row = SiteSettings.get_solo()
    row.site_header = "Runtime Header Name"
    row.save()

    response = _client_for(superuser).get(reverse("admin:index"))

    assert b"Runtime Header Name" in response.content


# -- obsolete admin assets are gone -----------------------------------------------------
def test_obsolete_admin_frontend_assets_are_removed():
    assert not (PROJECT_ROOT / "frontend" / "src" / "admin.js").exists()
    assert not (PROJECT_ROOT / "frontend" / "src" / "styles" / "admin.css").exists()
    assert "admin.js" not in (PROJECT_ROOT / "vite.config.js").read_text(
        encoding="utf-8"
    )


def test_no_template_or_source_references_legacy_admin_classes():
    roots = [
        PROJECT_ROOT / "templates",
        PROJECT_ROOT / "frontend",
        PROJECT_ROOT / "apps",
    ]
    legacy = re.compile(r"mf-admin-|mf-review-workflow|build/admin\.")
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".html", ".css", ".js", ".py", ".txt"}
        and legacy.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []


def test_console_templates_are_hand_built_not_a_third_party_theme():
    """Admin template overrides live in templates/admin; no theme package ships any."""
    templates = PROJECT_ROOT / "templates"
    assert not (templates / REMOVED_THEME).exists()
    assert (templates / "admin" / "base.html").is_file()
    assert (templates / "console" / "components").is_dir()
    assert not [
        str(path.relative_to(PROJECT_ROOT))
        for path in (templates / "admin").rglob("*.html")
        if re.search(
            rf"{{%\s*extends\s+[\"']{REMOVED_THEME}", path.read_text(encoding="utf-8")
        )
    ]
