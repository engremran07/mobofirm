"""Guide-request search step: related articles, typo tolerance and no gated text."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.blog import guide_requests_search as search
from apps.blog.models import Post, PostAccess, PostStatus
from apps.firmwares.models import Brand, Chipset, Model, Variant

pytestmark = pytest.mark.django_db
SECRET = "SECRETGATEDBODYTEXT"


@pytest.fixture
def catalogue():
    brand = Brand.objects.create(name="Infinix", slug="infinix")
    chipset = Chipset.objects.create(
        vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
    )
    model = Model.objects.create(
        brand=brand,
        name="Hot 40",
        slug="hot-40",
        model_code="X6836",
        model_codes=["X6836B"],
        chipset="MediaTek Helio G99",
        chipset_ref=chipset,
    )
    sibling = Model.objects.create(
        brand=brand,
        name="Note 30",
        slug="note-30",
        model_code="X6711",
        chipset="MediaTek Helio G99",
        chipset_ref=chipset,
    )
    other_brand = Brand.objects.create(name="Samsung", slug="samsung")
    other = Model.objects.create(
        brand=other_brand, name="Galaxy A14", slug="galaxy-a14", model_code="SM-A145F"
    )
    variant = Variant.objects.create(
        model=model, name="AI2401 India", slug="ai2401", model_code="AI2401_A"
    )
    return {
        "brand": brand,
        "chipset": chipset,
        "model": model,
        "sibling": sibling,
        "other": other,
        "variant": variant,
    }


@pytest.fixture
def author(django_user_model):
    return django_user_model.objects.create_user(
        username="writer", email="writer@example.com", password="pw-12345-abc"
    )


def make_post(author, title, **extra):
    extra.setdefault("status", PostStatus.PUBLISHED)
    return Post.objects.create(
        title=title, body=f"<p>{SECRET} body text</p>", author=author, **extra
    )


def titles(query):
    return [item.title for item in search.find_related_posts(query)]


def test_a_device_code_is_required_before_searching():
    assert search.DeviceQuery(keywords="frp").has_device is False
    assert search.DeviceQuery(variant_code="AI2401").has_device is True
    assert search.find_related_posts(search.DeviceQuery(keywords="frp")) == []


def test_same_model_posts_are_listed_with_their_access(author, catalogue):
    free = make_post(author, "Hot 40 FRP bypass", firmware_model=catalogue["model"])
    gated = make_post(
        author,
        "Hot 40 premium unlock",
        firmware_model=catalogue["model"],
        access=PostAccess.PAID,
        price=Decimal("3.00"),
    )
    make_post(author, "Unrelated Samsung note", firmware_model=catalogue["other"])

    rows = search.find_related_posts(search.DeviceQuery(model_code="X6836"))

    assert {row.post_id for row in rows} == {free.pk, gated.pk}
    by_title = {row.title: row for row in rows}
    assert by_title["Hot 40 premium unlock"].access == "paid"
    assert by_title["Hot 40 premium unlock"].access_label == "Paid"
    assert by_title["Hot 40 FRP bypass"].reasons[0] == "Same model"
    assert by_title["Hot 40 FRP bypass"].device == "Infinix Hot 40"


def test_unpublished_posts_are_never_listed(author, catalogue):
    make_post(
        author,
        "Draft Hot 40 trick",
        firmware_model=catalogue["model"],
        status=PostStatus.DRAFT,
    )

    assert titles(search.DeviceQuery(model_code="X6836")) == []


def test_variant_code_finds_the_variant_post_first_then_the_model(author, catalogue):
    exact = make_post(author, "AI2401_A flashing method")
    model_post = make_post(
        author, "Hot 40 generic guide", firmware_model=catalogue["model"]
    )

    rows = search.find_related_posts(search.DeviceQuery(variant_code="AI2401_A"))

    assert [row.post_id for row in rows][:2] == [exact.pk, model_post.pk]
    assert rows[0].reasons[0] == "Same variant code"
    assert "Same model" in rows[1].reasons


def test_same_chipset_and_brand_posts_are_listed_lower(author, catalogue):
    sibling_post = make_post(
        author, "Note 30 unlock pattern", firmware_model=catalogue["sibling"]
    )
    brand_post = make_post(
        author, "Infinix general tips", firmware_brand=catalogue["brand"]
    )

    rows = search.find_related_posts(search.DeviceQuery(model_code="X6836"))

    by_id = {row.post_id: row for row in rows}
    assert "Same chipset" in by_id[sibling_post.pk].reasons
    assert by_id[brand_post.pk].reasons == ("Same brand",)
    assert by_id[sibling_post.pk].score > by_id[brand_post.pk].score


def test_typed_chipset_and_keywords_widen_the_results(author, catalogue):
    tagged = make_post(author, "Helio G99 scatter fix")

    rows = search.find_related_posts(
        search.DeviceQuery(model_code="X6836", chipset="helio g99", keywords="scatter")
    )

    row = next(item for item in rows if item.post_id == tagged.pk)
    assert "Matches your keywords" in row.reasons


def test_typos_still_find_the_model_and_its_posts(author, catalogue):
    post = make_post(author, "Galaxy A14 FRP bypass", firmware_model=catalogue["other"])

    exact = titles(search.DeviceQuery(model_code="SM A145F"))
    typo = titles(search.DeviceQuery(model_code="Galxy A14"))

    assert exact == [post.title]
    assert typo == [post.title]


def test_results_never_carry_the_body_or_a_snippet(author, catalogue):
    make_post(
        author,
        "Hot 40 paid guide",
        firmware_model=catalogue["model"],
        access=PostAccess.FEATURED,
        price=Decimal("2.00"),
    )

    rows = search.find_related_posts(search.DeviceQuery(model_code="X6836"))

    assert rows
    for row in rows:
        assert SECRET not in repr(row)
    assert {field for field in search.RelatedPost.__slots__} == {
        "post_id",
        "title",
        "url",
        "access",
        "device",
        "reasons",
        "score",
    }


def test_the_body_text_is_never_searched(author, catalogue):
    make_post(author, "Plain title", access=PostAccess.FEATURED, price=Decimal(1))

    rows = search.find_related_posts(
        search.DeviceQuery(model_code="X6836", keywords=SECRET)
    )

    assert rows == []


def test_the_token_is_bound_to_the_member_and_the_search():
    query = search.DeviceQuery(model_code="X6836", keywords="frp")
    token = search.make_token(7, query, 3)

    assert search.read_token(token, 7) == search.Proof(query)
    assert search.read_token(token, 8) is None
    assert search.read_token(token + "x", 7) is None
    assert search.read_token(search.make_token(7, search.DeviceQuery(), 0), 7) is None


def test_the_article_token_proves_the_article_instead_of_a_search():
    token = search.make_token(7, search.DeviceQuery(), 0, post_id=41)

    assert search.read_token(token, 7) == search.Proof(search.DeviceQuery(), 41)


def test_initial_for_links_exact_catalogue_matches(catalogue):
    initial = search.initial_for(
        search.DeviceQuery(model_code="x 6836", variant_code="AI2401_A")
    )

    assert initial["model"] == catalogue["model"].pk
    assert initial["brand"] == catalogue["brand"].pk
    assert initial["chipset"] == catalogue["chipset"].pk
    assert initial["variant_code"] == "AI2401_A"


def test_initial_from_post_prefills_the_article_and_device(author, catalogue):
    post = make_post(author, "Hot 40 FRP bypass", firmware_model=catalogue["model"])

    initial = search.initial_from_post(post)

    assert initial["related_post"] == post.pk
    assert initial["model"] == catalogue["model"].pk
    assert initial["model_code"] == "X6836"
    assert initial["title"].startswith("Another method for: Hot 40")
