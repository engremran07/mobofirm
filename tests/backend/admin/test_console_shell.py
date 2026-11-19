"""Staff-console shell and component contract (templates/admin base, console layout, components).

Covers what every console page inherits: landmarks and hooks of the shell, the HTMX content-only
fragment, the lazy dashboard widgets and their permissions, the header search, the staff error pages
and the reusable components of ``templates/console/components``.
"""

from __future__ import annotations

import re

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.core.paginator import Paginator
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from app.views import csrf_failure
from apps.core.views import error_500_view
from tests.backend.admin.theme_guard import mentions_removed_theme

INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", re.IGNORECASE
)
INLINE_HANDLER = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE)
STYLE_ATTRIBUTE = re.compile(r"<[a-z][^>]*\sstyle\s*=", re.IGNORECASE)
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(name="superuser")
def superuser_fixture(django_user_model):
    return django_user_model.objects.create_superuser(
        username="shell-owner", email="shell-owner@example.test"
    )


@pytest.fixture(name="plain_staff")
def plain_staff_fixture(django_user_model):
    return django_user_model.objects.create_user(
        username="shell-staff",
        email="shell-staff@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _render(source: str, **context: object) -> str:
    return Template("{% load console_ui %}" + source).render(Context(context))


# -- shell ------------------------------------------------------------------------------
@pytest.mark.django_db
def test_shell_has_landmarks_hooks_and_no_inline_code(superuser):
    response = _client(superuser).get(reverse("admin:index"))
    html = response.content.decode()

    assert response.status_code == 200
    assert '<a class="skip-link" href="#console-main"' in html
    assert re.search(r'<header class="app-header" role="banner"', html)
    assert '<main class="app-main" id="console-main"' in html
    assert "<footer" in html
    for hook in (
        "data-console-sidebar-toggle",
        "data-console-sidebar-overlay",
        'id="console-dialog"',
        'id="console-toasts"',
        "data-console-toast-template",
        'id="console-palette"',
        'name="csrf-token"',
        'name="console-login-url"',
    ):
        assert hook in html, hook
    assert INLINE_SCRIPT.findall(html) == []
    assert INLINE_HANDLER.findall(html) == []
    assert STYLE_ATTRIBUTE.findall(html) == []
    assert not mentions_removed_theme(html)
    assert "javascript:" not in html.lower()


@pytest.mark.django_db
def test_sidebar_marks_exactly_one_current_page_and_groups_work_without_js(superuser):
    html = _client(superuser).get(reverse("admin:index")).content.decode()
    sidebar = html[html.index("<aside") : html.index("</aside>")]

    assert sidebar.count('aria-current="page"') == 1
    assert "<details" not in sidebar  # groups are disclosure buttons, driven by js/components/sidebar.js
    assert re.search(r'<button type="button" class="nav-link c-nav-toggle[^"]*" id="nav-toggle-([\w-]+)" aria-expanded="(?:true|false)" aria-controls="nav-panel-\1"', sidebar)
    assert re.search(r'class="c-nav-panel" id="nav-panel-[\w-]+" role="group" aria-labelledby="nav-toggle-', sidebar)
    assert 'data-console-nav-group="' in sidebar
    assert re.search(r'<a class="nav-link active" href="/admin/"', sidebar)


@pytest.mark.django_db
def test_htmx_requests_get_the_content_only_fragment(superuser):
    client = _client(superuser)
    url = reverse("admin:index")

    fragment = client.get(url, **HTMX).content.decode()
    assert "<title>" in fragment and "c-dashboard" in fragment
    assert "<aside" not in fragment and "<html" not in fragment

    for header in ({"HTTP_HX_BOOSTED": "true"}, {"HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}):
        full = client.get(url, **HTMX, **header).content.decode()
        assert "<aside" in full and "<html" in full, header


@pytest.mark.django_db
def test_django_messages_become_toasts_in_the_shell(superuser, rf):
    request = rf.get("/admin/")
    request.user = superuser

    html = render_to_string(
        "admin/base_site.html",
        {
            "title": "Messages",
            "messages": [
                Message(constants.SUCCESS, "All saved."),
                Message(constants.ERROR, "That failed."),
            ],
        },
        request=request,
    )

    assert 'data-console-toast-level="success"' in html and "All saved." in html
    assert 'data-console-toast-level="danger"' in html and "That failed." in html


# -- dashboard --------------------------------------------------------------------------
@pytest.mark.django_db
def test_dashboard_renders_instantly_with_lazy_widget_placeholders(superuser):
    html = _client(superuser).get(reverse("admin:index")).content.decode()

    assert "c-greeting__title" in html
    for slug in ("overview", "attention", "activity", "health"):
        url = reverse("admin:console_dashboard_widget", args=[slug])
        assert f'hx-get="{url}"' in html, slug
    assert html.count("c-skeleton-group") >= 4
    assert "<noscript>" in html


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["overview", "attention", "activity", "health"])
def test_each_widget_is_a_fragment_over_htmx_and_a_page_otherwise(superuser, slug):
    client = _client(superuser)
    url = reverse("admin:console_dashboard_widget", args=[slug])

    fragment = client.get(url, **HTMX)
    page = client.get(url)

    assert fragment.status_code == page.status_code == 200
    assert f'id="widget-{slug}"' in fragment.content.decode()
    assert "<html" not in fragment.content.decode()
    assert "<aside" in page.content.decode()
    assert f'id="widget-{slug}"' in page.content.decode()


@pytest.mark.django_db
def test_widgets_follow_permissions_and_unknown_slugs_are_404(superuser, plain_staff):
    activity = reverse("admin:console_dashboard_widget", args=["activity"])

    assert _client(superuser).get(activity, **HTMX).status_code == 200
    assert _client(plain_staff).get(activity, **HTMX).status_code == 404
    assert (
        _client(plain_staff)
        .get(reverse("admin:console_dashboard_widget", args=["attention"]), **HTMX)
        .status_code
        == 200
    )
    missing = reverse("admin:console_dashboard_widget", args=["nope"])
    assert _client(superuser).get(missing, **HTMX).status_code == 404


@pytest.mark.django_db
def test_widgets_and_search_require_staff():
    anonymous = Client()
    for url in (
        reverse("admin:console_dashboard_widget", args=["overview"]),
        reverse("admin:console_search"),
    ):
        response = anonymous.get(url, **HTMX, follow=True)
        assert response.redirect_chain[0][1] == 302
        assert response.redirect_chain[-1][0].startswith(reverse("users:account_login"))


# -- header search ------------------------------------------------------------------------
@pytest.mark.django_db
def test_search_filters_destinations_for_the_palette_and_has_a_page(superuser):
    client = _client(superuser)
    url = reverse("admin:console_search")

    palette = client.get(url, {"q": "blog"}, **HTMX).content.decode()
    assert "c-palette__item" in palette and "Blog" in palette
    assert "<html" not in palette

    short = client.get(url, {"q": "b"}, **HTMX).content.decode()
    assert "two letters" in short and "c-palette__group" in short

    page = client.get(url, {"q": "blog"})
    assert page.status_code == 200 and "<aside" in page.content.decode()


@pytest.mark.django_db
def test_search_escapes_what_was_typed(superuser):
    response = _client(superuser).get(
        reverse("admin:console_search"), {"q": '<script>alert("x")</script>'}, **HTMX
    )

    assert b"<script>" not in response.content


# -- error pages --------------------------------------------------------------------------
@pytest.mark.django_db
def test_console_errors_use_staff_pages_and_public_errors_stay_public(superuser):
    client = _client(superuser)

    staff = client.get("/admin/definitely-missing/")
    public = client.get("/definitely-missing/")

    assert staff.status_code == 404
    assert "admin/404.html" in [t.name for t in staff.templates]
    assert "c-error__title" in staff.content.decode()
    assert "errors/404.html" in [t.name for t in public.templates]


@pytest.mark.django_db
def test_signed_in_non_staff_users_see_the_staff_403_page():
    member = get_user_model().objects.create_user(
        username="plain-member", email="plain-member@example.test"
    )

    response = _client(member).get(reverse("admin:index"), follow=True)

    assert response.status_code == 403
    assert "admin/403.html" in [t.name for t in response.templates]
    assert "c-error__code" in response.content.decode()


@pytest.mark.django_db
def test_500_and_csrf_failure_pages_render_without_the_shell(rf):
    request = rf.get("/admin/anything/")
    request.user = get_user_model()()

    server_error = error_500_view(request)
    expired = csrf_failure(request, reason="token missing")

    assert server_error.status_code == 500 and expired.status_code == 403
    assert b"c-error__title" in server_error.content
    assert b"c-error__title" in expired.content
    assert b"<aside" not in server_error.content


# -- components ---------------------------------------------------------------------------
def test_components_escape_their_inputs():
    html = _render('{% ui "badge" label=label variant="success" %}', label="<b>x</b>")

    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>" not in html


def test_dialog_with_an_action_is_a_csrf_protected_form():
    html = _render(
        '{% component "dialog" title="Delete post" danger=True action="/x/" submit_label="Delete" %}'
        "Really?{% endcomponent %}",
        csrf_token="tok123",
    )

    assert "data-console-dialog-content" in html
    assert 'method="post"' in html and 'action="/x/"' in html
    assert 'name="csrfmiddlewaretoken" value="tok123"' in html
    assert "c-dialog--danger" in html and "btn-danger" in html
    assert 'data-ui-dismiss="modal"' in html


def test_dialog_without_an_action_has_no_form():
    html = _render(
        '{% component "dialog" title="Heads up" cancel_url="/back/" %}Hi{% endcomponent %}'
    )

    assert "<form" not in html and 'href="/back/"' in html


def test_dropdown_posts_through_forms_and_marks_danger():
    items = [
        {"label": "Edit", "url": "/e/"},
        {"divider": True},
        {"label": "Remove", "url": "/r/", "post": True, "variant": "danger"},
    ]

    html = _render(
        '{% ui "kebab_menu" label="Actions for X" items=items %}',
        items=items,
        csrf_token="tok",
    )

    assert "c-kebab" in html and 'aria-label="Actions for X"' in html
    assert '<a class="dropdown-item" href="/e/">' in html
    assert "dropdown-divider" in html
    assert 'method="post" action="/r/"' in html and "is-danger" in html
    assert 'value="tok"' in html


def test_navigation_tabs_mark_the_current_one():
    items = [
        {"title": "All", "link": "/a/", "active": False},
        {"title": "Pending", "link": "/b/", "active": True, "badge": 3},
    ]

    html = _render('{% ui "tabs" items=items label="Status" %}', items=items)

    assert html.count('aria-current="page"') == 1
    assert 'aria-label="Status"' in html and "c-tabs__badge" in html


def test_in_page_tabs_show_a_dot_on_panes_with_errors():
    panes = [{"id": "p1", "title": "Basics"}, {"id": "p2", "title": "SEO", "errors": 2}]

    html = _render(
        '{% component "tabs" panes=panes %}{% ui "tab_panel" id="p1" content="A" %}{% endcomponent %}',
        panes=panes,
    )

    assert 'role="tablist"' in html and html.count("data-console-tab ") == 2
    assert html.count("data-has-errors") == 1
    assert 'aria-selected="true"' in html and "2 problems" in html
    assert 'id="p1" aria-labelledby="p1-tab"' in html


def test_pagination_keeps_filters_and_supports_htmx(rf):
    page = Paginator(range(100), 10).page(3)
    request = rf.get("/list/", {"q": "x y", "page": 3})

    plain = _render('{% ui "pagination" page_obj=page %}', page=page, request=request)
    htmx = _render(
        '{% ui "pagination" page_obj=page hx_target="#results" %}', page=page, request=request
    )
    single = _render(
        '{% ui "pagination" page_obj=page %}',
        page=Paginator(range(3), 10).page(1),
        request=request,
    )

    assert 'aria-current="page"' in plain and "Showing 21&ndash;30 of 100" in plain
    assert "?q=x+y&amp;page=2" in plain and "?q=x+y&amp;page=4" in plain
    assert 'rel="prev"' in plain and 'rel="next"' in plain and "hx-get" not in plain
    assert 'hx-get="?q=x+y&amp;page=2"' in htmx and 'hx-target="#results"' in htmx
    assert single.strip() == ""


def test_data_table_scrolls_in_a_labelled_region_and_has_an_empty_state():
    columns = [
        {"label": "Name", "sorted": "ascending", "sort_url": "?o=1"},
        {"label": "Total", "numeric": True},
    ]

    table = _render(
        '{% component "data_table" columns=columns label="Orders" count=1 %}<tr><td>A</td><td>1</td></tr>{% endcomponent %}',
        columns=columns,
    )
    empty = _render(
        '{% component "data_table" columns=columns label="Orders" count=0 empty_title="No orders" %}{% endcomponent %}',
        columns=columns,
    )

    assert 'role="region" tabindex="0" aria-label="Orders"' in table
    assert 'aria-sort="ascending"' in table and "c-table__sort" in table
    assert "<table" not in empty and "No orders" in empty


class _SampleForm(forms.Form):
    title = forms.CharField(help_text="Shown in lists.")
    live = forms.BooleanField(required=False)


def test_form_field_wires_label_help_and_errors():
    form = _SampleForm(data={"title": "", "live": "on"})
    form.is_valid()

    title = _render('{% ui "form_field" field=field %}', field=form["title"])
    live = _render('{% ui "form_field" field=field wide=True %}', field=form["live"])

    assert 'for="id_title"' in title and "c-field--invalid" in title
    assert 'aria-invalid="true"' in title and "This field is required." in title
    assert 'id="id_title_helptext"' in title and 'role="alert"' in title
    assert "c-field--inline" in live and "c-field--wide" in live and "form-check-label" in live


def test_filter_chip_is_a_removable_link_or_a_toggle():
    removable = _render('{% ui "filter_chip" label="Status" value="Pending" remove_url="?" %}')
    toggle = _render('{% ui "filter_chip" label="Mine" url="?m=1" active=True %}')

    assert 'href="?"' in removable and "<strong>Pending</strong>" in removable
    assert 'aria-pressed="true"' in toggle


def test_theme_toggle_offers_light_dark_and_system():
    html = _render('{% ui "theme_toggle" %}')

    for choice in ("light", "dark", "auto"):
        assert f'data-console-theme-set="{choice}"' in html
    assert 'role="group"' in html and html.count("aria-pressed=") == 3


def test_attributes_cannot_inject_handlers_or_markup():
    escaped = _render(
        '{% ui "button" label="Go" attrs=attrs %}', attrs={"hx-get": '"><script>x</script>'}
    )

    assert "<script>" not in escaped
    with pytest.raises(Exception, match="not allowed"):
        _render('{% ui "button" label="Go" attrs=attrs %}', attrs={"onclick": "x()"})
