"""Unified list search: chipsets, did-you-mean, typo tolerance, HTMX fragments."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.search import (
    did_you_mean,
    list_search_context,
    search,
    search_queryset,
)
from apps.core.search.trigram import trigram_available, trigram_candidates
from apps.firmwares.models import Brand, Chipset, FlashingTool, FlashingToolCategory
from apps.firmwares.search_providers import CHIPSET_ANNOTATIONS, CHIPSET_FIELDS

pytestmark = pytest.mark.django_db

CHIPSET_URL = "firmwares:chipset_list"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Suggestion vocabularies are cached; start every test empty."""
    cache.clear()


@pytest.fixture(name="chipsets")
def _chipsets() -> dict[str, Chipset]:
    """Three chipsets with aliases (code names) for two vendors."""
    return {
        "d9200": Chipset.objects.create(
            vendor="MediaTek",
            family="Dimensity",
            name="Dimensity 9200",
            slug="mediatek-dimensity-9200",
            aliases=["MT6985"],
        ),
        "d9300": Chipset.objects.create(
            vendor="MediaTek",
            family="Dimensity",
            name="Dimensity 9300",
            slug="mediatek-dimensity-9300",
        ),
        "sd8g2": Chipset.objects.create(
            vendor="Qualcomm",
            family="Snapdragon",
            name="Snapdragon 8 Gen 2",
            slug="qualcomm-snapdragon-8-gen-2",
            aliases=["SM8550"],
        ),
    }


def _names(response) -> list[str]:
    return [chipset.name for chipset in response.context["chipsets"]]


def _search_chipsets(query: str):
    return search_queryset(
        Chipset.objects.all(),
        query,
        CHIPSET_FIELDS,
        annotations=CHIPSET_ANNOTATIONS,
    )


@pytest.mark.usefixtures("chipsets")
def test_chipset_list_filters_numeric_query_server_side() -> None:
    """The reported case: ?q=9200 narrows the chipset list on the server."""
    response = Client().get(reverse(CHIPSET_URL), {"q": "9200"})
    assert _names(response) == ["Dimensity 9200"]
    assert b"Dimensity 9200" in response.content
    assert b"Dimensity 9300" not in response.content


@pytest.mark.usefixtures("chipsets")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("MT6985", ["Dimensity 9200"]),
        ("mt6985", ["Dimensity 9200"]),
        ("SM8550", ["Snapdragon 8 Gen 2"]),
        ("Dimensity 9200", ["Dimensity 9200"]),
        ("mediatek 9200", ["Dimensity 9200"]),
        ("mtk", ["Dimensity 9200", "Dimensity 9300"]),
        ("qualcomm", ["Snapdragon 8 Gen 2"]),
        ("snapdargon", ["Snapdragon 8 Gen 2"]),
    ],
)
def test_chipset_query_matches_name_vendor_alias_codename_and_typos(
    query: str, expected: list[str]
) -> None:
    """Names, vendors, code names, synonyms and typos all find the chipset."""
    response = Client().get(reverse(CHIPSET_URL), {"q": query})
    assert _names(response) == expected


@pytest.mark.usefixtures("chipsets")
def test_chipset_typo_shows_corrected_notice() -> None:
    """A typo-tolerant match tells the visitor which spelling was used."""
    response = Client().get(reverse(CHIPSET_URL), {"q": "snapdargon"})
    assert response.context["search_corrected"] == "snapdragon"
    assert b"data-search-corrected" in response.content


@pytest.mark.usefixtures("chipsets")
def test_numbers_are_not_treated_as_typos() -> None:
    """9400 must not fall back to 9200 or 9300."""
    response = Client().get(reverse(CHIPSET_URL), {"q": "9400"})
    assert _names(response) == []


@pytest.mark.usefixtures("chipsets")
def test_chipset_list_keeps_vendor_filter_with_query() -> None:
    """The vendor filter combines with the query."""
    client = Client()
    none = client.get(reverse(CHIPSET_URL), {"q": "9", "vendor": "Qualcomm"})
    assert _names(none) == []
    hit = client.get(reverse(CHIPSET_URL), {"q": "gen", "vendor": "Qualcomm"})
    assert _names(hit) == ["Snapdragon 8 Gen 2"]


@pytest.mark.usefixtures("chipsets")
def test_htmx_request_gets_fragment_only_and_vary_header() -> None:
    """HTMX gets the fragment; the full page wires per-keystroke search."""
    client = Client()
    url = reverse(CHIPSET_URL)
    fragment = client.get(url, {"q": "9200"}, headers={"HX-Request": "true"})
    assert b"<html" not in fragment.content
    assert b"Dimensity 9200" in fragment.content
    assert "HX-Request" in fragment["Vary"]
    full = client.get(url, {"q": "9200"})
    assert b"<html" in full.content
    assert b'hx-trigger="input changed delay:250ms' in full.content
    assert b"data-live-search" in full.content
    assert b'aria-live="polite"' in full.content


@pytest.mark.usefixtures("chipsets")
def test_chipset_page_without_query_lists_everything() -> None:
    """No query means the whole catalogue."""
    assert len(_names(Client().get(reverse(CHIPSET_URL)))) == 3


@pytest.mark.usefixtures("chipsets")
def test_did_you_mean_labels_the_entity_type() -> None:
    """Suggestions carry the entity type: 'Samsung (brand)'."""
    Brand.objects.create(name="Samsung", slug="samsung")
    suggestions = did_you_mean("samung")
    assert [(item.title, item.noun) for item in suggestions] == [("Samsung", "brand")]
    assert suggestions[0].label == "Samsung (brand)"
    assert suggestions[0].url == reverse("firmwares:brand_detail", args=["samsung"])
    chip = did_you_mean("snapdargon")
    assert chip
    assert all(item.noun == "chipset" for item in chip)


def test_did_you_mean_suggests_firmware_type() -> None:
    """Firmware classifications are suggested with their own label."""
    suggestions = did_you_mean("enginering")
    assert [(item.title, item.noun) for item in suggestions] == [
        ("Engineering", "firmware type")
    ]
    assert "type=engineering" in suggestions[0].url


@pytest.mark.usefixtures("chipsets")
def test_did_you_mean_ignores_exact_names_and_digit_swaps() -> None:
    """Exact names and different numbers are never 'corrections'."""
    assert not did_you_mean("dimensity 9200")
    assert not did_you_mean("9100")


def test_did_you_mean_hides_unlisted_rows() -> None:
    """Inactive catalogue rows are never suggested."""
    Brand.objects.create(name="Samsung", slug="samsung")
    hidden_category = FlashingToolCategory.objects.create(
        name="Hidden", slug="hidden", is_active=False
    )
    FlashingTool.objects.create(
        category=hidden_category, name="Secretflasher", slug="secretflasher"
    )
    assert not did_you_mean("secretflashr")


def test_vocabulary_is_cached_and_invalidated_on_save() -> None:
    """Saving or deleting a watched row refreshes the suggestion vocabulary."""
    assert not did_you_mean("zephyrix")
    Brand.objects.create(name="Zephyrix", slug="zephyrix")
    assert [item.title for item in did_you_mean("zephyrx")] == ["Zephyrix"]
    Brand.objects.filter(slug="zephyrix").delete()
    assert not did_you_mean("zephyrx")


@pytest.mark.usefixtures("chipsets")
def test_header_search_falls_back_to_typo_tolerance() -> None:
    """Few strict hits trigger the typo pass, ranked below strict hits."""
    typo = search("snapdargon")
    assert [hit.title for hit in typo] == ["Qualcomm Snapdragon 8 Gen 2"]
    strict = search("snapdragon")
    assert strict
    assert strict[0].score > typo[0].score


@pytest.mark.usefixtures("chipsets")
def test_header_search_resolves_codename_synonym() -> None:
    """A SoC part number finds the marketing name."""
    assert [hit.title for hit in search("MT6985")] == ["MediaTek Dimensity 9200"]


def test_firmware_type_is_searchable_by_synonym() -> None:
    """'stock' finds the official firmware classification."""
    assert "Official firmware" in [hit.title for hit in search("stock")]


@pytest.mark.usefixtures("chipsets")
def test_search_queryset_without_query_is_unfiltered() -> None:
    """An empty query leaves the list untouched."""
    result = _search_chipsets("")
    assert result.queryset.count() == 3
    assert not result.is_active


@pytest.mark.usefixtures("chipsets")
def test_list_search_context_offers_suggestions_only_when_few() -> None:
    """Suggestions appear only when a query finds fewer than three rows."""
    Brand.objects.create(name="Samsung", slug="samsung")
    assert list_search_context(_search_chipsets("samung"), 0)["did_you_mean"]
    assert not list_search_context(_search_chipsets(""), 3)["did_you_mean"]


@pytest.mark.usefixtures("chipsets")
def test_chipset_empty_state_keeps_request_button_and_suggestion() -> None:
    """Empty results show the suggestion and keep the request button."""
    Brand.objects.create(name="Samsung", slug="samsung")
    response = Client().get(reverse(CHIPSET_URL), {"q": "samung"})
    assert b"data-did-you-mean" in response.content
    assert b"Samsung" in response.content
    assert b'data-request-cta="chipset"' in response.content


@pytest.mark.usefixtures("chipsets")
def test_chipset_pagination_links_keep_query_and_filters() -> None:
    """Page links carry q and vendor and are HTMX enabled."""
    for number in range(60):
        Chipset.objects.create(
            vendor="Unisoc", name=f"Tiger {number}", slug=f"unisoc-tiger-{number}"
        )
    response = Client().get(reverse(CHIPSET_URL), {"q": "tiger", "vendor": "Unisoc"})
    html = response.content.decode()
    assert "page=2" in html
    assert "q=tiger" in html
    assert 'hx-target="#chipset-results"' in html


@pytest.mark.usefixtures("chipsets")
def test_trigram_probe_is_off_on_sqlite_and_query_compiles() -> None:
    """SQLite uses the Python fallback; the PostgreSQL query still compiles."""
    assert trigram_available() is False
    query = trigram_candidates(
        Chipset.objects.all(), ("name", "vendor"), ("snapdargon",)
    )
    assert "SIMILARITY" in str(query.query).upper()


@pytest.mark.usefixtures("chipsets")
def test_annotations_cast_aliases_to_searchable_text() -> None:
    """Code names stored in the JSON aliases column are searchable."""
    result = _search_chipsets("mt6985")
    assert [chipset.name for chipset in result.queryset] == ["Dimensity 9200"]
