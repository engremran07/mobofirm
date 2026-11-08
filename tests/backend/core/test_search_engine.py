"""Central search engine: registry, providers, permissions, ranking, endpoints."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Category, Post, PostStatus
from apps.core.geo import (
    country_from_accept_language,
    country_from_language_tag,
    country_from_timezone,
)
from apps.core.search import (
    SearchContext,
    SearchHit,
    SearchProvider,
    get_providers,
    register,
    search,
    search_grouped,
    unregister,
)
from apps.firmwares.models import (
    Brand,
    Chipset,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    Variant,
)
from apps.pages.models import Page
from apps.tags.models import Tag

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def catalog():
    brand = Brand.objects.create(name="Zephyrix", slug="zephyrix")
    chipset = Chipset.objects.create(
        vendor="Quantum", family="Qx", name="Qx 900", slug="quantum-qx-900"
    )
    model = Model.objects.create(
        brand=brand,
        name="Zephyr One",
        slug="zephyr-one",
        model_code="ZX-100",
        chipset_ref=chipset,
    )
    hidden_model = Model.objects.create(
        brand=brand, name="Zephyr Secret", slug="zephyr-secret", is_active=False
    )
    variant = Variant.objects.create(
        model=model, name="Global Edition", slug="global", region="EU"
    )
    category = FlashingToolCategory.objects.create(name="Official", slug="official")
    tool = FlashingTool.objects.create(
        category=category, name="Zephyr Flasher", slug="zephyr-flasher"
    )
    approved = OfficialFirmware.objects.create(
        original_file_name="zephyr-approved.zip",
        stored_file_path="/firmware/zephyr-approved.zip",
        brand=brand,
        model=model,
        is_verified=True,
    )
    pending = OfficialFirmware.objects.create(
        original_file_name="zephyr-pending.zip",
        stored_file_path="/firmware/zephyr-pending.zip",
        brand=brand,
        model=model,
        is_verified=False,
    )
    return {
        "brand": brand,
        "chipset": chipset,
        "model": model,
        "hidden_model": hidden_model,
        "variant": variant,
        "tool": tool,
        "approved": approved,
        "pending": pending,
    }


def kinds_of(hits: list[SearchHit]) -> set[str]:
    return {hit.kind for hit in hits}


def titles(hits: list[SearchHit]) -> list[str]:
    return [hit.title for hit in hits]


def test_firmware_providers_return_every_catalog_kind(catalog):
    hits = search("zephyr", limit=50)
    assert {"brand", "model", "variant", "tool", "firmware"} <= kinds_of(hits)
    chipset_hits = search("qx 900", limit=20)
    assert "chipset" in kinds_of(chipset_hits)
    by_kind = {hit.kind: hit for hit in hits}
    assert by_kind["brand"].url == reverse("firmwares:brand_detail", args=["zephyrix"])
    assert by_kind["model"].url == reverse(
        "firmwares:model_detail", args=["zephyrix", "zephyr-one"]
    )
    assert by_kind["firmware"].url.startswith("/firmwares/file/")


def test_only_public_approved_content_is_returned(catalog):
    hits = search("zephyr", limit=50)
    assert "zephyr-approved.zip" in titles(hits)
    assert "zephyr-pending.zip" not in titles(hits)
    assert "Zephyr Secret" not in titles(hits)


def test_model_and_variant_pickers_are_narrowed_by_filters(catalog):
    other = Brand.objects.create(name="Other Brand", slug="other-brand")
    Model.objects.create(brand=other, name="Zephyr Clone", slug="zephyr-clone")
    brand_id = str(catalog["brand"].pk)
    models = search("zephyr", kinds=["model"], filters={"brand": brand_id})
    assert titles(models) == ["Zephyr One"]
    assert models[0].value == str(catalog["model"].pk)
    by_slug = search("zephyr", kinds=["model"], filters={"brand": "other-brand"})
    assert titles(by_slug) == ["Zephyr Clone"]
    variants = search(
        "global", kinds=["variant"], filters={"model": str(catalog["model"].pk)}
    )
    assert titles(variants) == ["Global Edition"]


def test_blog_tag_and_page_providers_respect_visibility(catalog):
    author = User.objects.create_user(username="writer", email="w@example.com")
    category = Category.objects.create(name="Flashing Guides")
    tag = Tag.objects.create(name="Bootloader", is_curated=True, is_active=True)
    post = Post.objects.create(
        title="Unlock the Zephyr bootloader",
        body="<p>Long enough body for the guide.</p>",
        author=author,
        category=category,
    )
    post.tags.add(tag)
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )
    draft = Post.objects.create(
        title="Unreleased Zephyr draft",
        body="<p>Long enough body for the draft.</p>",
        author=author,
    )
    Page.objects.create(
        slug="zephyr-help",
        title="Zephyr help",
        content="x",
        status=Page.Status.PUBLISHED,
    )
    Page.objects.create(
        slug="zephyr-staff",
        title="Zephyr staff notes",
        content="x",
        status=Page.Status.PUBLISHED,
        access_level=Page.Access.STAFF,
    )

    hits = search("zephyr", limit=50)
    assert "Unlock the Zephyr bootloader" in titles(hits)
    assert draft.title not in titles(hits)
    assert "Zephyr help" in titles(hits)
    assert "Zephyr staff notes" not in titles(hits)
    assert "Flashing Guides" in titles(search("flashing", kinds=["blog_category"]))
    assert "Bootloader" in titles(search("bootloader", kinds=["tag"]))
    # An unpublished-only tag is not offered.
    Tag.objects.create(name="Lonely", is_curated=True, is_active=True)
    assert search("lonely", kinds=["tag"]) == []

    staff = User.objects.create_user(
        username="staffer", email="s@example.com", is_staff=True
    )
    assert "Zephyr staff notes" in titles(search("zephyr", limit=50, user=staff))


def test_staff_only_kinds_are_hidden_from_everyone_else(catalog):
    User.objects.create_user(username="zoe-account", email="zoe@example.com")
    staff = User.objects.create_user(
        username="zed-admin", email="zed@example.com", is_staff=True
    )
    member = User.objects.create_user(username="zed-member", email="m@example.com")

    assert search("zed", user=staff) and kinds_of(search("zed", user=staff)) == {"user"}
    assert search("zed", user=member) == []
    assert search("zed") == []
    assert search("zed", kinds=["user"], user=member) == []
    assert "user" not in {p.kind for p in get_providers(member)}
    assert "user" in {p.kind for p in get_providers(staff)}


def test_countries_and_calling_codes_are_pick_only_and_typed_ahead():
    assert search("pak") == []  # not navigable: absent from global search
    countries = search("pak", kinds=["country"])
    assert countries[0].value == "PK"
    assert countries[0].label == "Pakistan (PK)"
    assert dict(countries[0].extra)["calling-code"] == "+92"
    one_letter = search("p", kinds=["country"], limit=20)
    assert one_letter and all("p" in hit.title.lower() for hit in one_letter)
    codes = search("pak", kinds=["calling_code"])
    assert codes[0].value == "+92"
    assert codes[0].label == "Pakistan (PK) +92"
    assert "United Kingdom (GB)" in titles(
        search("+44", kinds=["calling_code"], limit=10)
    )
    assert search("cote", kinds=["country"])[0].value == "CI"  # accent-insensitive
    browse = search("", kinds=["country"], limit=5)
    assert len(browse) == 5


def test_ranking_prefers_exact_then_prefix_then_substring(catalog):
    Brand.objects.create(name="Atlas", slug="atlas")
    Brand.objects.create(name="Atlas Mobile", slug="atlas-mobile")
    Brand.objects.create(name="Grand Atlas", slug="grand-atlas")
    ranked = titles(search("atlas", kinds=["brand"]))
    assert ranked == ["Atlas", "Atlas Mobile", "Grand Atlas"]


def test_empty_and_short_queries_do_not_hit_the_catalog(catalog):
    assert search("") == []
    assert search("   ") == []
    # Catalog pickers (brand/model/variant) answer from one character; the rest want two.
    assert kinds_of(search("z")) <= {"brand", "model", "variant"}
    assert search("zephyr nonsense", kinds=["firmware"]) == []
    assert search(None) == []
    # Pickers name their kinds and may browse before anything is typed.
    assert "Zephyrix" in titles(search("", kinds=["brand"]))


def test_every_word_must_match(catalog):
    assert search("zephyr nonsense", kinds=["model"]) == []
    assert titles(search("zephyr one", kinds=["model"])) == ["Zephyr One"]


def test_registry_rejects_conflicting_kinds_and_accepts_custom_providers():
    class First(SearchProvider):
        kind = "unit_test_kind"
        label = "Unit"
        label_plural = "Units"

        def search(self, context: SearchContext):
            return [SearchHit(kind=self.kind, title="Hello", score=1.0)]

    class Second(First):
        pass

    register(First())
    try:
        register(First())  # same class: idempotent
        with pytest.raises(ValueError):
            register(Second())
        assert titles(search("hello", kinds=["unit_test_kind"])) == ["Hello"]
        assert "unit_test_kind" in {group.kind for group in search_grouped("hello")}
    finally:
        unregister("unit_test_kind")


def test_failing_provider_does_not_break_the_search(catalog):
    from django.db import DatabaseError

    class Broken(SearchProvider):
        kind = "unit_broken"
        label = "Broken"
        label_plural = "Broken"

        def search(self, context: SearchContext):
            raise DatabaseError("boom")

    register(Broken())
    try:
        assert "brand" in kinds_of(search("zephyr"))
    finally:
        unregister("unit_broken")


def test_suggest_endpoint_renders_an_accessible_listbox(catalog, client: Client):
    response = client.get(
        reverse("search:suggest"), {"q": "zephyr", "id": "site-search"}
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert 'role="option"' in html
    assert 'id="site-search-option-0"' in html
    assert 'aria-selected="false"' in html
    assert "See all results for" in html
    assert "Cache-Control" in response.headers


def test_suggest_pick_mode_exposes_values_and_data_attributes(client: Client):
    response = client.get(
        reverse("search:suggest"),
        {"q": "pak", "kinds": "country", "mode": "pick", "id": "id_country"},
    )
    html = response.content.decode()
    assert 'data-value="PK"' in html
    assert 'data-label="Pakistan (PK)"' in html
    assert 'data-calling-code="+92"' in html
    assert "<a " not in html  # no navigation links in pick mode


def test_suggest_escapes_queries_and_titles(catalog, client: Client):
    Brand.objects.create(name='<script>alert("x")</script>', slug="xss-brand")
    payload = "<script>alert(1)</script>"
    empty = client.get(reverse("search:suggest"), {"q": payload}).content.decode()
    assert payload not in empty
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in empty
    found = client.get(reverse("search:suggest"), {"q": "script"}).content.decode()
    assert '<script>alert("x")</script>' not in found
    assert "&lt;script&gt;" in found
    page = client.get(reverse("search:results"), {"q": payload}).content.decode()
    assert payload not in page


def test_suggest_sanitises_prefix_kinds_and_limit(catalog, client: Client):
    response = client.get(
        reverse("search:suggest"),
        {"q": "zephyr", "id": '"><x', "kinds": "BRAND, bad kind!", "limit": "nope"},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert 'id="combobox-option-0"' in html
    assert "<x" not in html


def test_results_page_groups_by_kind_and_filters(catalog, client: Client):
    response = client.get(reverse("search:results"), {"q": "zephyr"})
    html = response.content.decode()
    assert response.status_code == 200
    assert 'name="robots" content="noindex' in html
    assert "Brands" in html and "Devices" in html
    assert 'aria-label="Breadcrumb"' in html
    only = client.get(reverse("search:results"), {"q": "zephyr", "kinds": "brand"})
    body = only.content.decode()
    assert "Zephyrix" in body and "Zephyr One" not in body
    partial = client.get(
        reverse("search:results"), {"q": "zephyr"}, HTTP_HX_REQUEST="true"
    )
    assert "<!doctype html>" not in partial.content.decode().lower()


def test_results_page_hides_pick_only_kinds(client: Client):
    page = client.get(reverse("search:results"), {"q": "pakistan"}).content.decode()
    assert "Countries" not in page
    forced = client.get(
        reverse("search:results"), {"q": "pakistan", "kinds": "country"}
    ).content.decode()
    assert (
        "Pakistan"
        not in forced.split('id="search-results"')[1].split("request-missing")[0]
    )


def test_empty_results_offer_request_ctas_with_the_typed_query(catalog, client: Client):
    page = client.get(reverse("search:results"), {"q": "Nonexistium"}).content.decode()
    assert "No results" in page
    for kind in ("brand", "model", "variant", "chipset", "firmware"):
        assert f'data-request-cta="{kind}"' in page
    # Signed out: sign in first, then return to the prefilled request form.
    assert "Sign in to request a brand" in page
    assert reverse("users:account_login") in page
    assert "Nonexistium" in page


def test_signed_in_users_get_direct_prefilled_request_links(catalog, client: Client):
    user = User.objects.create_user(username="asker", email="asker@example.com")
    client.force_login(user)
    page = client.get(reverse("search:results"), {"q": "Nonexistium"}).content.decode()
    assert "Request a brand" in page
    assert "Sign in to request" not in page
    assert f"{reverse('firmwares:request_brand')}?name=Nonexistium" in page


def test_suggest_empty_state_lists_request_ctas(catalog, client: Client):
    html = client.get(
        reverse("search:suggest"), {"q": "Nonexistium", "id": "site-search"}
    ).content.decode()
    assert "No matches for" in html
    assert 'data-request-cta="chipset"' in html
    footer = client.get(
        reverse("search:suggest"), {"q": "zephyr", "id": "site-search"}
    ).content.decode()
    assert "Can’t find it? Request it" in footer
    assert "#request-missing" in footer


def test_legacy_universal_search_url_still_resolves(catalog, client: Client):
    response = client.get(reverse("firmwares:universal_search"), {"q": "zephyr"})
    assert response.status_code == 200


def test_geo_lookups():
    assert country_from_timezone("Asia/Karachi") == "PK"
    assert country_from_timezone("Asia/Calcutta") == "IN"  # legacy alias
    assert country_from_timezone("Mars/Base") is None
    assert country_from_timezone("") is None
    assert country_from_language_tag("en-PK") == "PK"
    assert country_from_language_tag("zh-Hans-CN") == "CN"
    assert country_from_language_tag("en") is None
    assert country_from_accept_language("en;q=0.5, fr-CA;q=0.9") == "CA"
    assert country_from_accept_language("") is None
