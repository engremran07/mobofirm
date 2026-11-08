"""Admin shell contract: access control, dashboard, sidebar coverage, CSP and branding."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.admin_navigation import (
    NAV_GROUPS,
    ModelEntry,
    build_sidebar,
    changelist_url,
    listed_model_labels,
    unlisted_models,
)
from apps.site_settings.models import SiteSettings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", re.IGNORECASE
)
NONCE = re.compile(r"'nonce-([^']+)'")


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="shell-admin", email="shell-admin@example.test"
    )


@pytest.fixture
def staff_without_permissions(db):
    return get_user_model().objects.create_user(
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
    assert dashboard.context["kpis"] == []
    links = _sidebar_links(dashboard)
    assert reverse("admin:index") in links
    assert not [
        link for link in links if link.startswith("/admin/") and link != "/admin/"
    ]

    for model in (model for model in admin.site._registry):
        url = reverse(
            f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
        )
        assert client.get(url).status_code == 403, model._meta.label


# -- dashboard and sidebar --------------------------------------------------------
@pytest.mark.django_db
def test_dashboard_renders_kpis_for_a_superuser(superuser):
    response = _client_for(superuser).get(reverse("admin:index"))

    assert response.status_code == 200
    assert b'id="kpi-pending_firmware"' in response.content
    assert b'id="kpi-pending_comments"' in response.content
    assert response.context["kpis"]


def test_every_registered_model_is_listed_in_the_sidebar():
    assert unlisted_models() == []


def test_sidebar_only_lists_models_that_are_registered():
    registered = {model._meta.label_lower for model in admin.site._registry}
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
        assert 'id="nav-sidebar-inner"' in body, path
        assert "Mobofirm" in body.split('id="nav-sidebar-apps"')[0], path


@pytest.mark.django_db
def test_sidebar_hides_models_the_user_cannot_view(staff_without_permissions):
    request = (
        _client_for(staff_without_permissions).get(reverse("admin:index")).wsgi_request
    )

    titles = [
        item["title"] for group in build_sidebar(request) for item in group["items"]
    ]

    assert "Blog posts" not in titles
    assert "Dashboard" in titles


# -- CSP --------------------------------------------------------------------------
@pytest.mark.django_db
def test_admin_csp_is_nonce_based_and_distinct_from_the_public_policy(superuser):
    client = _client_for(superuser)
    public = client.get("/").headers["Content-Security-Policy"]
    response = client.get(reverse("admin:index"))
    policy = response.headers["Content-Security-Policy"]

    assert "'unsafe-eval'" not in public
    assert "'unsafe-eval'" in policy
    assert "'unsafe-inline'" not in policy.split("style-src-attr")[0]
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "http:" not in policy
    assert " * " not in f" {policy} "
    nonce = NONCE.search(policy)
    assert nonce, policy
    assert f'nonce="{nonce.group(1)}"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["index", "blog_post_changelist", "blog_post_add"])
def test_admin_pages_have_no_inline_script_without_a_nonce(superuser, name):
    response = _client_for(superuser).get(reverse(f"admin:{name}"))

    assert response.status_code == 200
    for match in INLINE_SCRIPT.finditer(response.content.decode()):
        assert "nonce=" in match.group(1), match.group(0)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["seo:dashboard", "ads:dashboard", "tags:manage"])
def test_staff_pages_get_the_policy_that_matches_their_shell(superuser, url_name):
    response = _client_for(superuser).get(reverse(url_name))

    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    uses_unfold = b"unfold/css/styles.css" in response.content
    assert ("'unsafe-eval'" in policy) == uses_unfold, url_name


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


def test_legacy_admin_template_overrides_are_gone():
    """Only the dashboard may shadow a stock admin template; the rest are new pages."""
    import unfold
    from django import contrib

    stock = {
        path.relative_to(root).as_posix()
        for root in (
            Path(contrib.__file__).parent / "admin" / "templates" / "admin",
            Path(unfold.__file__).parent / "templates" / "admin",
        )
        for path in root.rglob("*.html")
    }
    templates = PROJECT_ROOT / "templates" / "admin"
    shadowing = {
        path.relative_to(templates).as_posix() for path in templates.rglob("*.html")
    } & stock
    assert shadowing == {"index.html"}, sorted(shadowing)
