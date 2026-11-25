"""Markup contract of the changelist filter bar (every admin_filters class shares it).

Each filter renders one ``div.c-fctl[data-ui-popover]`` with a trigger button wired to its
panel by ``aria-controls``; choice filters expose a listbox of ``role=option`` links that
carry ``aria-selected`` / ``data-selected``, form filters expose a labelled dialog whose
form keeps the other active query parameters. The Django query-string contract (the
``query_string`` of each choice, ``<field>_from`` / ``<field>_to``, ``__icontains``) is
rendered unchanged.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

CHOICES = [
    {"selected": False, "query_string": "?", "display": "All"},
    {"selected": True, "query_string": "?status__exact=1", "display": "Published"},
    {"selected": False, "query_string": "?status__exact=2", "display": "Draft"},
]
RANGE_CHOICE = {
    "selected": True,
    "query_string": "?",
    "display": "All",
    "hidden": [("q", "firmware")],
}


def _render(template: str, **extra) -> str:
    context = {
        "title": "Status",
        "uid": "filter-status",
        "active": True,
        "cl": None,
        "path": "/admin/blog/post/",
        "choices": CHOICES,
    }
    context.update(extra)
    return render_to_string(template, context)


def _contract(html: str, popup: str) -> None:
    assert html.count("data-ui-popover") == 1
    assert 'class="c-fctl"' in html
    trigger = re.search(r"<button[^>]*data-ui-trigger[^>]*>", html)
    assert trigger, "the filter has no trigger button"
    assert 'aria-expanded="false"' in trigger.group(0)
    assert f'aria-haspopup="{popup}"' in trigger.group(0)
    controls = re.search(r'aria-controls="([^"]+)"', trigger.group(0))
    assert controls and f'id="{controls.group(1)}"' in html
    assert re.search(r"data-ui-panel[^>]*\bhidden\b", html)
    assert "data-active" in html.split("data-ui-trigger")[0]


@pytest.mark.parametrize(
    "template",
    [
        "console/filters/_choices.html",
        "console/filters/choices_dropdown.html",
        "console/filters/related_dropdown.html",
    ],
)
def test_choice_filters_render_an_accessible_listbox(template):
    html = _render(template)

    _contract(html, "listbox")
    assert 'role="listbox"' in html
    options = re.findall(r"<a role=\"option\"[^>]*>", html)
    assert len(options) == len(CHOICES)
    assert all('tabindex="-1"' in option for option in options)
    selected = [option for option in options if "data-selected" in option]
    assert len(selected) == 1 and 'aria-selected="true"' in selected[0]
    # The Django query-string contract is untouched and each option refreshes the results over HTMX.
    assert 'href="?status__exact=2"' in html
    assert 'hx-get="?status__exact=2"' in html
    assert 'hx-target="#c-cl-results"' in html
    assert '<span class="c-fctl__value">Published</span>' in html


def test_choice_filter_without_a_selection_shows_no_value():
    html = _render("console/filters/_choices.html", active=False)

    assert "c-fctl__value" not in html
    assert "data-active" not in html.split("data-ui-trigger")[0]


@pytest.mark.parametrize(
    ("template", "input_type"),
    [
        ("console/filters/range_date.html", "date"),
        ("console/filters/range_datetime.html", "datetime-local"),
        ("console/filters/range_numeric.html", "number"),
    ],
)
def test_range_filters_render_a_dialog_with_a_get_form(template, input_type):
    spec = SimpleNamespace(
        name_from="created_from",
        name_to="created_to",
        value_from="1",
        value_to="9",
        input_type=input_type,
        title="Created",
    )
    html = _render(template, spec=spec, choices=[RANGE_CHOICE], title="Created")

    _contract(html, "dialog")
    assert 'role="dialog"' in html and 'aria-label="Created"' in html
    assert '<form method="get" action="/admin/blog/post/"' in html
    assert 'name="created_from"' in html and 'name="created_to"' in html
    assert f'type="{input_type}"' in html
    assert '<input type="hidden" name="q" value="firmware">' in html


@pytest.mark.parametrize(
    "template", ["console/filters/text.html", "console/filters/field_text.html"]
)
def test_text_filters_render_a_dialog_with_a_labelled_search_input(template):
    spec = SimpleNamespace(parameter_name="title__icontains", value="flash")
    html = _render(template, spec=spec, choices=[RANGE_CHOICE], title="Title")

    _contract(html, "dialog")
    assert 'name="title__icontains"' in html and 'value="flash"' in html
    assert re.search(
        r'<label class="visually-hidden" for="filter-status-text">Title</label>', html
    )
    assert '<input type="hidden" name="q" value="firmware">' in html
    assert "Clear" in html


def test_staff_pages_with_filter_forms_share_the_filter_bar_chrome():
    from pathlib import Path

    for template in (
        "seo/inventory.html",
        "seo/issues.html",
        "seo/ledger.html",
        "seo/links.html",
        "ads/discoveries.html",
        "ads/placement_manager.html",
        "storage/status.html",
    ):
        source = Path("templates", template).read_text(encoding="utf-8")
        assert 'class="c-fbar c-fbar--form"' in source, template
        assert "align-items-end" not in source, (
            f"{template} still hand-builds its filter row"
        )


def test_bulk_action_select_is_a_listbox_over_the_native_select():
    html = render_to_string(
        "console/components/listbox.html",
        {
            "field": SimpleNamespace(
                value="b",
                auto_id="id_action",
                label="Action",
                field=SimpleNamespace(
                    choices=[("", "- Select -"), ("b", "Delete"), ("c", "Archive")]
                ),
            ),
            "label": "Action",
        },
    )

    assert 'role="listbox"' in html and 'aria-haspopup="listbox"' in html
    assert html.count('role="option"') == 3
    assert html.count("data-selected") == 1 and 'data-value="b"' in html
    assert (
        'aria-controls="id_action-listbox"' in html and 'id="id_action-listbox"' in html
    )


def test_filter_bar_ids_stay_stable_for_the_htmx_out_of_band_swap():
    from pathlib import Path

    changelist = Path("templates/console/crud/_changelist.html").read_text(
        encoding="utf-8"
    )
    hx = Path("templates/console/crud/_hx_list.html").read_text(encoding="utf-8")

    assert 'id="c-cl-filters"' in changelist and "data-ui-sheet" in changelist
    assert "#c-cl-filters:outerMorph" in hx
    assert 'id="c-cl-status"' in changelist and 'aria-live="polite"' in changelist
    assert "data-console-clear-selection" in changelist
    assert "c-drawer" not in changelist
