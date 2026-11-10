"""Tags, blog journal, marketplace, wallet history and My files use the one engine."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Category, Post, PostAccess, PostStatus
from apps.core.search import get_provider, search, search_items
from apps.firmwares.models import Brand, Chipset
from apps.marketplace.models import Listing, SellerProfile
from apps.marketplace.pickers import search_options
from apps.tags.models import Tag
from apps.users.library import LibraryEntry, LibraryFilters, search_library
from apps.wallet import services as wallet_services
from apps.wallet.models import LedgerReason

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Suggestion vocabularies are cached; start every test empty."""
    cache.clear()


def _user(name: str):
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@example.test", email_verified_at=timezone.now()
    )


def _publish(post: Post, *, published: bool = True) -> None:
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED if published else PostStatus.DRAFT,
        is_published=published,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )


def test_tag_list_filters_with_synonyms_typos_and_fragment() -> None:
    """Topics filter server-side; typos are corrected; HTMX gets the grid only."""
    Tag.objects.create(name="Bootloader unlock", is_curated=True)
    Tag.objects.create(name="Fastboot", is_curated=True)
    Tag.objects.create(name="Hidden idea", ai_suggested=True)
    client = Client()
    url = reverse("tags:list")
    page = client.get(url, {"q": "bootloader"})
    assert [tag.name for tag in page.context["tags"]] == ["Bootloader unlock"]
    typo = client.get(url, {"q": "fastbot"})
    assert [tag.name for tag in typo.context["tags"]] == ["Fastboot"]
    assert typo.context["search_corrected"] == "fastboot"
    fragment = client.get(url, {"q": "fastboot"}, headers=HX)
    assert b"<html" not in fragment.content
    assert b"Fastboot" in fragment.content
    assert b"Hidden idea" not in client.get(url, {"q": "hidden"}).content


def test_tag_json_autocomplete_uses_the_engine() -> None:
    """The tag autocomplete tolerates typos like every other list."""
    Tag.objects.create(name="Fastboot", is_curated=True)
    payload = Client().get(reverse("tags:search"), {"q": "fastbot"}).json()
    assert [item["name"] for item in payload["items"]] == ["Fastboot"]


def test_journal_search_finds_typos_suggests_and_hides_unpublished() -> None:
    """Journal search: typo tolerant, shows 'did you mean', no drafts."""
    brand = Brand.objects.create(name="Samsung", slug="samsung")
    author = _user("journal-author")
    live = Post.objects.create(
        title="Flashing guide for Samsung phones",
        body="<p>body</p>",
        author=author,
        firmware_brand=brand,
    )
    draft = Post.objects.create(
        title="Secret Samsung draft", body="<p>body</p>", author=author
    )
    _publish(live)
    _publish(draft, published=False)
    client = Client()
    url = reverse("blog:post_list")
    strict = client.get(url, {"q": "samsung"})
    assert [card.title for card in strict.context["cards"]] == [
        "Flashing guide for Samsung phones"
    ]
    typo = client.get(url, {"q": "samung"})
    assert typo.context["total"] == 1
    assert typo.context["search_corrected"] == "samsung"
    assert [s.title for s in typo.context["did_you_mean"]] == ["Samsung"]
    fragment = client.get(url, {"q": "samung"}, headers=HX)
    assert b"<html" not in fragment.content
    assert b"data-did-you-mean" in fragment.content
    assert b"Secret Samsung draft" not in strict.content


def test_journal_search_never_matches_gated_body_text() -> None:
    """A featured post's hidden body cannot be found, even with typos."""
    author = _user("gated-author")
    post = Post.objects.create(
        title="Premium unlock",
        body="<p>intro</p><p>uniquehiddenword</p>",
        author=author,
        access=PostAccess.FEATURED,
    )
    _publish(post)
    client = Client()
    for query in ("uniquehiddenword", "uniquehiddenwrod"):
        assert client.get(reverse("blog:post_list"), {"q": query}).context["total"] == 0
    assert client.get(reverse("blog:post_list"), {"q": "premium"}).context["total"] == 1


def test_journal_provider_and_category_vocabulary() -> None:
    """Categories are suggested by name once they have live posts."""
    category = Category.objects.create(name="Bootloaders", slug="bootloaders")
    post = Post.objects.create(
        title="Unlock", body="<p>x</p>", author=_user("cat-author"), category=category
    )
    _publish(post)
    assert [hit.title for hit in search("bootloadrs", kinds=["blog_category"])] == [
        "Bootloaders"
    ]


def _seller(name: str, *, verified: bool) -> SellerProfile:
    return SellerProfile.objects.create(
        user=_user(name), display_name=name.title(), verified=verified
    )


def test_marketplace_search_lists_only_approved_listings_of_verified_sellers() -> None:
    """Listing search is typo tolerant and never leaks pending/unverified items."""
    verified = _seller("verified-seller", verified=True)
    unverified = _seller("shady-seller", verified=False)
    Listing.objects.create(
        seller=verified,
        title="Odin flasher pack",
        price=Decimal(5),
        status=Listing.Status.APPROVED,
    )
    Listing.objects.create(seller=verified, title="Odin pending pack", price=Decimal(5))
    Listing.objects.create(
        seller=unverified,
        title="Odin shady pack",
        price=Decimal(5),
        status=Listing.Status.APPROVED,
    )
    client = Client()
    url = reverse("marketplace:listing_browse")
    page = client.get(url, {"q": "odin"})
    assert [item.title for item in page.context["listings"]] == ["Odin flasher pack"]
    typo = client.get(url, {"q": "flashr"}, headers=HX)
    assert b"Odin flasher pack" in typo.content
    assert b"<html" not in typo.content
    assert b"Odin shady pack" not in client.get(url, {"q": "shady"}).content
    assert [hit.title for hit in search("odin", kinds=["listing"])] == [
        "Odin flasher pack"
    ]
    assert get_provider("listing") is not None


def test_marketplace_pickers_use_the_engine_and_skip_selected() -> None:
    """Seller speciality pickers share ranking, typos and browse lists."""
    samsung = Brand.objects.create(name="Samsung", slug="samsung")
    Brand.objects.create(name="Xiaomi", slug="xiaomi")
    chip = Chipset.objects.create(
        vendor="Qualcomm", family="Snapdragon", name="Snapdragon 8 Gen 2", slug="sd8"
    )
    assert [o.label for o in search_options("brands", "samung", [])] == ["Samsung"]
    assert not search_options("brands", "samsung", [samsung.pk])
    assert len(search_options("brands", "", [])) == 2
    assert [o.pk for o in search_options("chipsets", "snapdargon", [])] == [chip.pk]
    assert len(search_options("chipsets", "", [])) == 1


def test_wallet_history_search_matches_notes_references_and_reasons() -> None:
    """The credit history searches notes, references and reason labels."""
    user = _user("wallet-owner")
    wallet_services.credit(
        user, 5, reason=LedgerReason.TOPUP, key="k1", note="Welcome bonus"
    )
    wallet_services.credit(
        user, 7, reason=LedgerReason.TOPUP, key="k2", reference="invoice-77"
    )
    client = Client()
    client.force_login(user)
    url = reverse("wallet:ledger")
    notes = client.get(url, {"q": "welcome"})
    assert [entry.amount for entry in notes.context["page_obj"]] == [5]
    reference = client.get(url, {"q": "invoice"}, headers=HX)
    assert b"<html" not in reference.content
    assert [entry.amount for entry in reference.context["page_obj"]] == [7]
    typo = client.get(url, {"q": "welcom"})
    assert [entry.amount for entry in typo.context["page_obj"]] == [5]
    assert len(client.get(url, {"q": "top"}).context["page_obj"]) == 2
    other = _user("wallet-other")
    other_client = Client()
    other_client.force_login(other)
    assert not other_client.get(url, {"q": "welcome"}).context["page_obj"]


def _entry(name: str, brand: str, device: str, tier: str = "free") -> LibraryEntry:
    now = timezone.now()
    return LibraryEntry(
        name=name,
        brand=brand,
        brand_slug=brand.lower(),
        device=device,
        variant="",
        type_label="Official",
        tier=tier,
        tier_label=tier,
        paid_amount=None,
        counted_in_quota=False,
        served_at=now,
        last_served_at=now,
        times_served=1,
        detail_url="",
        download_url="",
        available=True,
    )


def test_library_search_words_synonyms_typos_and_other_filters() -> None:
    """My files text search matches words, typos and combines with filters."""
    entries = [
        _entry("S911BXXU1AWB1.zip", "Samsung", "Galaxy S23"),
        _entry("fw-redmi.zip", "Xiaomi", "Redmi Note 12", tier="paid"),
    ]
    by_words = search_library(entries, LibraryFilters(query="galaxy s23"))
    assert [e.name for e in by_words.items] == ["S911BXXU1AWB1.zip"]
    typo = search_library(entries, LibraryFilters(query="samung"))
    assert [e.brand for e in typo.items] == ["Samsung"]
    assert typo.corrected == "samsung"
    combined = search_library(entries, LibraryFilters(query="note", tier="paid"))
    assert [e.brand for e in combined.items] == ["Xiaomi"]
    assert not search_library(entries, LibraryFilters(query="note", tier="free")).items
    assert len(search_library(entries, LibraryFilters()).items) == 2


def test_search_items_keeps_order_and_reports_corrections() -> None:
    """In-memory lists keep their order and never invent matches."""
    items = [
        SimpleNamespace(text=value) for value in ("Alpha one", "Beta two", "Alpha")
    ]
    outcome = search_items(items, "alpha", lambda item: (item.text,))
    assert [item.text for item in outcome.items] == ["Alpha one", "Alpha"]
    typo = search_items(items, "bbeta", lambda item: (item.text,))
    assert [item.text for item in typo.items] == ["Beta two"]
    assert typo.corrected == "beta"
    assert not search_items(items, "zzzz", lambda item: (item.text,)).items
    assert search_items(items, "", lambda item: (item.text,)).items == items
