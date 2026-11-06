"""Contract tests for the Django-rendered frontend shell, partials and tokens."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django import forms
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core.cache import cache
from django.core.paginator import Paginator
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import NoReverseMatch, reverse

from apps.pages.seed import seed_pages

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PROJECT_ROOT / "templates"
FRONTEND = PROJECT_ROOT / "frontend" / "src"
SHELL_TEMPLATE_DIRS = (
    "includes",
    "components",
    "account",
    "allauth",
    "errors",
    "users",
    "devices",
)


def _shell_templates() -> list[Path]:
    paths = [TEMPLATES / "base.html"]
    for directory in SHELL_TEMPLATE_DIRS:
        paths.extend((TEMPLATES / directory).rglob("*.html"))
    return paths


def _request(path: str = "/"):
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    request.session = SessionStore()
    return request


def test_every_template_icon_is_registered_in_the_icon_set():
    """Each Lucide icon used in a template is registered in the icon module."""
    icon_source = (FRONTEND / "modules" / "icons.js").read_text(encoding="utf-8")
    registered = set(
        re.findall(
            r"^  ([A-Za-z0-9]+),$", icon_source.split("const iconSet")[1], re.MULTILINE
        )
    )
    used: dict[str, str] = {}
    for template in TEMPLATES.rglob("*.html"):
        for name in re.findall(
            r'(?:data-lucide|(?<![-\w])icon)="([a-z0-9-]+)"',
            template.read_text(encoding="utf-8"),
        ):
            pascal = "".join(part.capitalize() for part in name.split("-"))
            used.setdefault(pascal, f"{template.relative_to(PROJECT_ROOT)}: {name}")

    missing = {name: where for name, where in used.items() if name not in registered}
    assert not missing, f"Unregistered Lucide icons: {missing}"


def test_shell_templates_only_reverse_existing_url_names():
    """Shell templates never reference an unknown ``{% url %}`` name."""
    pattern = re.compile(r"""\{%\s*url\s+['"]([\w:.-]+)['"]""")
    missing: list[str] = []
    for template in _shell_templates():
        for name in set(pattern.findall(template.read_text(encoding="utf-8"))):
            try:
                reverse(name)
            except NoReverseMatch as error:
                if "is not a valid view function or pattern name" in str(error):
                    missing.append(f"{template.relative_to(PROJECT_ROOT)}: {name}")
    assert not missing, f"Templates reverse unknown URL names: {missing}"


def test_dead_templates_are_removed():
    """Retired device and user templates stay deleted."""
    dead = [
        "devices/browse.html",
        "devices/search.html",
        "devices/brand_detail.html",
        "devices/model_detail.html",
        "devices/fragments/brand_list.html",
        "devices/fragments/model_list.html",
        "devices/fragments/search_results.html",
        "users/device_approval_needed.html",
        "users/device_eviction.html",
        "users/device_mfa_challenge.html",
    ]
    assert [name for name in dead if (TEMPLATES / name).exists()] == []


@pytest.mark.django_db
def test_header_has_navigation_search_and_current_section(client):
    """The header exposes navigation, search and the current-section marker."""
    response = client.get(reverse("firmwares:brand_list"))
    html = response.content.decode()

    assert response.status_code == 200
    for label in ("Firmware", "Brands", "Chipsets", "Tools &amp; Guides", "Blog"):
        assert f">{label}</a>" in html
    assert re.search(
        r'<a class="site-nav-link" href="/firmwares/brands/" aria-current="page">'
        r"Brands</a>",
        html,
    )
    assert 'aria-current="page">Firmware<' not in html
    assert 'role="search"' in html
    assert f'hx-get="{reverse("firmwares:api_search_autocomplete")}"' in html
    assert 'role="combobox"' in html
    assert 'id="global-feedback"' in html
    assert 'hx-indicator="#global-progress"' in html


@pytest.mark.django_db
def test_footer_links_privacy_terms_consent_and_status(client):
    """Published legal pages and the health probe are linked from the footer."""
    seed_pages(site_name="Mobofirm", contact_email="legal@example.com")
    html = client.get(reverse("home")).content.decode()

    for slug in ("privacy", "terms", "consent"):
        assert f'href="/pages/{slug}/"' in html
        assert client.get(f"/pages/{slug}/").status_code == 200
    assert f'href="{reverse("health-live")}"' in html


@pytest.mark.django_db
def test_footer_never_links_to_unpublished_legal_pages(client):
    """Legal pages that are not published are not linked from the footer."""
    cache.clear()
    html = client.get(reverse("home")).content.decode()

    for slug in ("privacy", "terms", "consent"):
        assert f'href="/pages/{slug}/"' not in html
    assert f'href="{reverse("health-live")}"' in html


@pytest.mark.django_db
def test_theme_initialiser_is_a_synchronous_head_script_with_guarded_storage(client):
    """The theme script is a blocking head script that guards storage access."""
    html = client.get(reverse("home")).content.decode()
    head = html.split("</head>")[0]
    source = (FRONTEND / "theme-init.js").read_text(encoding="utf-8")

    assert re.search(r'<script src="[^"]*theme-init\.js"></script>', head)
    assert "defer" not in head.split("theme-init.js")[1].split(">")[0]
    assert "try {" in source and "catch" in source
    assert "prefers-color-scheme" in source
    assert "<script>" not in html


@pytest.mark.django_db
def test_device_registration_prompt_has_no_blocking_backdrop_element():
    """The device prompt is a modal dialog without a separate backdrop element."""
    request = _request()
    request.session.update(
        {
            "device_registration_prompt": {
                "can_approve": True,
                "device_name": "Test device",
                "os_family": "Windows",
                "browser_family": "Chrome",
                "active_devices": 0,
                "max_devices": 3,
            }
        }
    )
    html = render_to_string("base.html", {}, request=request)

    assert "data-device-registration-dialog" in html
    assert "device-registration-backdrop" not in html
    site_css = (FRONTEND / "styles" / "site.css").read_text(encoding="utf-8")
    assert "device-registration-backdrop" not in site_css


@pytest.mark.django_db
def test_pagination_partial_preserves_the_query_string():
    """Pagination links keep the existing query string and support HTMX targets."""
    page = Paginator(list(range(100)), 10).page(3)
    html = render_to_string(
        "components/pagination.html",
        {"page_obj": page},
        request=_request("/blog/?category=news&sort=old&page=3"),
    )

    assert 'href="?category=news&amp;sort=old&amp;page=4"' in html
    assert 'href="?category=news&amp;sort=old&amp;page=2"' in html
    assert 'aria-current="page"' in html
    assert "hx-get" not in html

    htmx_html = render_to_string(
        "components/pagination.html",
        {"page_obj": page, "hx_target": "#results"},
        request=_request("/blog/?category=news&page=3"),
    )
    assert 'hx-target="#results"' in htmx_html
    assert 'hx-push-url="true"' in htmx_html


@pytest.mark.django_db
def test_pagination_partial_renders_nothing_for_a_single_page():
    """A single page produces no pagination markup."""
    page = Paginator(list(range(3)), 10).page(1)
    html = render_to_string(
        "components/pagination.html", {"page_obj": page}, request=_request()
    )
    assert not html.strip()


class _ContractForm(forms.Form):
    name = forms.CharField(help_text="Shown publicly.")
    agree = forms.BooleanField()
    kind = forms.ChoiceField(choices=[("a", "A")], required=False)


def test_form_field_partial_wires_labels_errors_and_descriptions():
    """The field partial links labels, help text and errors accessibly."""
    form = _ContractForm(data={"name": "", "agree": "on"})
    form.is_valid()
    html = render_to_string("components/form_field.html", {"field": form["name"]})

    assert '<label class="form-label" for="id_name">' in html
    assert 'aria-describedby="id_name_help id_name_errors"' in html
    assert 'aria-invalid="true"' in html
    assert 'id="id_name_errors"' in html and 'id="id_name_help"' in html
    assert "form-control is-invalid" in html

    checkbox = render_to_string("components/form_field.html", {"field": form["agree"]})
    assert "form-check-input" in checkbox and "form-check-label" in checkbox
    select = render_to_string("components/form_field.html", {"field": form["kind"]})
    assert "form-select" in select


def test_form_errors_partial_marks_non_field_errors_as_alerts():
    """Non-field errors render with the alert role."""
    form = _ContractForm(data={})
    form.add_error(None, "Something is wrong.")
    html = render_to_string("components/form_errors.html", {"form": form})
    assert 'role="alert"' in html and "Something is wrong." in html


def test_table_scroll_tag_creates_a_focusable_labelled_region():
    """The table scroll tag wraps tables in a keyboard-focusable labelled region."""
    html = Template(
        '{% load mf_ui %}{% table_scroll "Firmware files" %}<table></table>'
        "{% endtable_scroll %}"
    ).render(Context())

    assert 'role="region"' in html and 'tabindex="0"' in html
    assert 'aria-label="Firmware files"' in html and "<table></table>" in html


@pytest.mark.django_db
def test_confirm_dialog_is_app_owned_and_csrf_protected():
    """The confirmation dialog is a native dialog element posting with a CSRF token."""
    request = _request()
    html = render_to_string(
        "components/confirm_dialog.html",
        {
            "dialog_id": "merge-confirm",
            "title": "Merge tags?",
            "message": "This cannot be undone.",
            "action": "/tags/merge/",
            "confirm_label": "Merge",
            "danger": True,
            "hidden_name": "slug",
            "hidden_value": "x",
        },
        request=request,
    )

    assert "<dialog" in html and "data-app-dialog" in html
    assert 'aria-labelledby="merge-confirm-title"' in html
    assert 'method="post"' in html and "csrfmiddlewaretoken" in html
    assert "btn-danger" in html and "data-dialog-close" in html


@pytest.mark.django_db
def test_allauth_pages_render_inside_the_site_shell(client):
    """Account pages use the public site header and footer."""
    response = client.get(reverse("account_reset_password_done"))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'class="site-header"' in html
    assert "allauth-shell" in html
    assert 'class="site-footer' in html


@pytest.mark.django_db
def test_not_found_page_offers_search_inside_the_shell(client):
    """The 404 page keeps the shell and offers search."""
    response = client.get("/definitely-not-a-route/")
    html = response.content.decode()

    assert response.status_code == 404
    assert "This page is unavailable." in html
    assert 'role="search"' in html
    assert 'class="site-header"' in html


@pytest.mark.django_db
def test_notification_detail_only_links_internal_urls():
    """Notification details link only to same-site URLs."""

    def render(url: str) -> str:
        notification = SimpleNamespace(
            pk=1,
            title="Hello",
            message="Body",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            url=url,
            is_read=True,
        )
        return render_to_string(
            "users/notifications/details.html", {"notification": notification}
        )

    assert "Open related item" in render("/blog/post/")
    for unsafe in ("javascript:alert(1)", "//evil.example/x", "https://evil.example/"):
        assert "Open related item" not in render(unsafe)


def _hex_to_luminance(value: str) -> float:
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_hex_to_luminance(foreground), _hex_to_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _theme_tokens() -> dict[str, dict[str, str]]:
    css = (FRONTEND / "styles" / "tokens.css").read_text(encoding="utf-8")
    themes: dict[str, dict[str, str]] = {}
    for selector, body in re.findall(r"(:root[^{]*)\{([^}]*)\}", css):
        if "data-bs-theme" not in selector and selector.strip() != ":root,":
            continue
        name = "dark" if "dark" in selector else "light"
        themes.setdefault(name, {}).update(
            dict(re.findall(r"--mf-([a-z-]+):\s*(#[0-9a-fA-F]{6});", body))
        )
    return themes


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_token_pairs_meet_wcag_aa_contrast(theme):
    """Foreground/background token pairs reach 4.5:1 contrast in both themes."""
    tokens = _theme_tokens()[theme]
    pairs = [
        ("text", "page"),
        ("text", "surface"),
        ("text-muted", "page"),
        ("text-muted", "surface"),
        ("accent", "page"),
        ("accent", "surface"),
        ("accent-ink", "accent"),
        ("accent-ink", "accent-strong"),
        ("danger", "page"),
        ("danger", "surface"),
        ("danger-ink", "danger"),
        ("success", "page"),
        ("warning", "page"),
        ("page", "text-muted"),
        ("page", "success"),
        ("page", "warning"),
        ("page", "danger"),
    ]
    failures = {
        f"{fg} on {bg}": round(_contrast(tokens[fg], tokens[bg]), 2)
        for fg, bg in pairs
        if _contrast(tokens[fg], tokens[bg]) < 4.5
    }
    assert not failures, f"{theme} theme contrast failures: {failures}"


def test_htmx_triggers_never_listen_to_the_first_input_in_the_document():
    """No template uses an unscoped ``from:input`` HTMX trigger."""
    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in TEMPLATES.rglob("*.html")
        if re.search(r"from:input\b", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_card_hover_lift_is_scoped_to_interactive_cards():
    """Hover lift applies only to link cards and ad placeholders stay collapsed."""
    css = (FRONTEND / "styles" / "site.css").read_text(encoding="utf-8")

    assert not re.search(r"(^|\n)\.surface-card:hover", css)
    assert "a.surface-card:hover" in css
    assert not re.search(r"\.ad-placeholder[^{]*\{[^}]*min-height", css)
    assert re.search(r"\.ad-placeholder\s*\{\s*display:\s*none", css)
