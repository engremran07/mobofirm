"""Header combobox partial: groups with icons, did-you-mean options, pick mode."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.firmwares.models import Brand, Model

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Suggestion vocabularies are cached; start every test empty."""
    cache.clear()


@pytest.fixture(name="catalog")
def _catalog() -> None:
    """One brand with one device."""
    brand = Brand.objects.create(name="Zephyrix", slug="zephyrix")
    Model.objects.create(brand=brand, name="Zephyr One", slug="zephyr-one")


def _suggest(**params: str):
    return Client().get(reverse("search:suggest"), {"id": "site-search", **params})


@pytest.mark.usefixtures("catalog")
def test_navigate_options_are_grouped_by_kind_with_icons() -> None:
    """Each kind gets a labelled group with its icon; options keep unique ids."""
    html = _suggest(q="zeph").content.decode()
    assert 'role="group"' in html
    assert html.count('class="combobox-group"') == 2
    assert 'id="site-search-group-brand"' in html
    assert 'id="site-search-group-model"' in html
    assert 'data-lucide="building-2"' in html
    assert 'data-lucide="smartphone"' in html
    assert html.count('role="option"') >= 2
    ids = [
        part.split('"')[0]
        for part in html.split('id="site-search-option-')[1:]
        if "request" not in part.split('"')[0]
    ]
    assert len(ids) == len(set(ids))


def test_did_you_mean_options_follow_the_listbox_contract() -> None:
    """Near misses are options linking to the entity, labelled with its type."""
    html = _suggest(q="enginering").content.decode()
    assert "Did you mean" in html
    assert 'data-suggestion-kind="firmware_type"' in html
    assert 'role="option"' in html
    assert "type=engineering" in html
    assert "Firmware type" in html


def test_did_you_mean_keeps_request_buttons_when_nothing_matches() -> None:
    """Suggestions never replace the request-a-... buttons of the empty state."""
    html = _suggest(q="enginering").content.decode()
    assert html.count("data-request-cta") == 5


@pytest.mark.usefixtures("catalog")
def test_no_suggestions_when_results_are_plentiful_or_exact() -> None:
    """Exact hits need no 'did you mean'."""
    assert "Did you mean" not in _suggest(q="zephyrix").content.decode()


@pytest.mark.usefixtures("catalog")
def test_pick_mode_has_no_groups_or_suggestions() -> None:
    """Pickers stay flat: options carry values and never suggestions."""
    html = _suggest(q="zephyrx", mode="pick", kinds="brand").content.decode()
    assert "combobox-group" not in html
    assert "Did you mean" not in html
    assert 'data-value="' in html


def test_results_page_shows_did_you_mean_and_popular_state() -> None:
    """The /search/ page suggests near misses and keeps the request section."""
    response = Client().get(reverse("search:results"), {"q": "enginering"})
    html = response.content.decode()
    assert "data-did-you-mean" in html
    assert "Engineering" in html
    assert 'id="request-missing"' in html


def test_site_search_has_slash_hint_and_global_marker() -> None:
    """The header form is marked for the '/' shortcut and shows the key hint."""
    html = Client().get("/").content.decode()
    assert "data-global-search" in html
    assert 'class="site-search__hint"' in html


def test_recent_search_opt_out_attribute_renders() -> None:
    """A combobox can opt out of recent searches per instance."""
    html = render_to_string(
        "components/combobox.html",
        {"input_id": "x-search", "mode": "navigate", "recent": "off"},
    )
    assert 'data-recent="false"' in html
    default = render_to_string(
        "components/combobox.html", {"input_id": "y-search", "mode": "navigate"}
    )
    assert "data-recent" not in default
