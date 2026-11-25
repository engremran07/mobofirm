"""Automatic in-article and in-feed ad slots through the real zone system."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from apps.ads.models import (
    AdCreative,
    AdEvent,
    AdPlacement,
    AdsSettings,
    Campaign,
    PlacementAssignment,
    TemplateAdExclusion,
)
from apps.ads.services import placement_manager
from apps.ads.services import zones as zone_service
from apps.blog.models import GuideRequest, Post, PostStatus
from apps.blog.rendering import render_article

pytestmark = pytest.mark.django_db

POST_PAGE = "blog/post_detail.html"
BRAND_PAGE = "firmwares/brand_list.html"
LANDING = "https://advertiser.example/offer"
WORDS = "word " * 80
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"


def paragraphs(count: int) -> str:
    return "".join(f"<p>Paragraph {i}. {WORDS}</p>\n" for i in range(1, count + 1))


@pytest.fixture(autouse=True)
def _consent(monkeypatch):
    monkeypatch.setattr(zone_service, "has_consent", lambda request, category: True)


@pytest.fixture(name="ads_on")
def ads_on_fixture():
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.excluded_user_roles = ""
    row.save()
    return row


@pytest.fixture(name="campaign")
def campaign_fixture(_ads_on):
    return Campaign.objects.create(name="House", type="house")


def make_creative(campaign, name: str, **extra) -> AdCreative:
    values = {
        "campaign": campaign,
        "name": name,
        "creative_type": "banner",
        "image_url": f"https://cdn.example/{name}.png",
        "click_url": LANDING,
        "headline": f"Headline {name}",
        "tagline": f"Tagline {name}",
    }
    values.update(extra)
    return AdCreative.objects.create(**values)


def make_placement(zone: str, page: str, creatives: list[AdCreative], **extra):
    placement = AdPlacement.objects.create(
        name=f"{page} {zone}",
        code=f"{page}-{zone}".replace("/", "-").replace(".", "-"),
        slug=f"{page}-{zone}".replace("/", "-").replace(".", "-"),
        template_reference=page,
        zone=zone,
        **extra,
    )
    for creative in creatives:
        PlacementAssignment.objects.create(placement=placement, creative=creative)
    return placement


@pytest.fixture(name="article_ads")
def article_ads_fixture(campaign):
    creatives = [make_creative(campaign, "one"), make_creative(campaign, "two")]
    return make_placement("in_article", POST_PAGE, creatives), creatives


@pytest.fixture(name="_article_ads")
def article_ads_for_side_effect(article_ads):
    """The in-article placement exists; the test does not need its objects."""
    return article_ads


@pytest.fixture(name="_tile_ads")
def tile_ads_for_side_effect(tile_ads):
    """The between-items placement exists; the test does not need its objects."""
    return tile_ads


@pytest.fixture(name="_ads_on")
def ads_on_for_side_effect(ads_on):
    """Ads are switched on; the test does not touch the settings row."""
    return ads_on


def render(rf, source: str, *, page: str, user=None, headers=None, **context) -> str:
    request = rf.get("/x/", **(headers or {"HTTP_USER_AGENT": DESKTOP}))
    request.user = user or AnonymousUser()
    return Template(source, name=page).render(Context({"request": request, **context}))


def article(rf, html: str, **kwargs) -> str:
    return render(
        rf,
        "{% load ad_tags %}{% ad_article_body html %}",
        page=kwargs.pop("page", POST_PAGE),
        html=mark_safe(html),
        **kwargs,
    )


def slots(html: str) -> int:
    return html.count('data-ad-zone="in_article"')


# ----------------------------------------------------------------- article ----


def test_slots_are_spliced_between_paragraphs_with_distinct_creatives(rf, _article_ads):
    source = paragraphs(10)

    html = article(rf, source)

    assert slots(html) == 2
    assert html.count("cdn.example/one.png") == 1
    assert html.count("cdn.example/two.png") == 1
    assert AdEvent.objects.filter(event_type="impression").count() == 2
    assert (
        re.sub(r"<aside class=\"ad-zone.*?</aside>", "", html, flags=re.DOTALL)
        == source
    )


def test_a_single_creative_is_never_repeated_on_one_page(rf, campaign):
    make_placement("in_article", POST_PAGE, [make_creative(campaign, "solo")])

    html = article(rf, paragraphs(14))

    assert slots(html) == 1
    assert AdEvent.objects.filter(event_type="impression").count() == 1


def test_the_slot_is_labelled_reserved_lazy_and_sponsored(rf, article_ads):
    html = article(rf, paragraphs(10))

    assert 'aria-label="Advertisement"' in html
    assert '<span class="ad-zone__label">Advertisement</span>' in html
    assert "ad-zone--s300x250" in html  # reserved size: no layout shift
    assert "ad-zone--inline" in html
    assert 'rel="noopener sponsored nofollow"' in html
    assert 'loading="lazy"' in html
    _placement, creatives = article_ads
    assert "/ads/go/" in html
    assert any(
        f"/{creative.pk}/" in html for creative in creatives
    )  # tracking redirect
    assert LANDING not in html


def test_the_dedupe_is_per_request_not_global(rf, _article_ads):
    first = article(rf, paragraphs(10))
    second = article(rf, paragraphs(10))

    assert slots(first) == slots(second) == 2


def test_the_same_request_never_gets_more_than_the_page_budget(
    rf, _article_ads, ads_on
):
    ads_on.max_ads_per_page = 1
    ads_on.save()

    assert slots(article(rf, paragraphs(14))) == 1


def test_placement_numbers_tune_the_spacing(rf, campaign):
    creatives = [make_creative(campaign, f"c{i}") for i in range(4)]
    make_placement(
        "in_article",
        POST_PAGE,
        creatives,
        auto_first_after=2,
        auto_every=3,
        auto_max=3,
        auto_min_items=4,
    )
    ads = AdsSettings.get_solo()
    ads.min_content_between_ads = 0
    ads.save()

    html = article(rf, paragraphs(12))

    parts = re.split(r"<aside class=\"ad-zone.*?</aside>", html, flags=re.DOTALL)
    assert [part.count("<p>") for part in parts] == [2, 3, 3, 4]


def test_a_short_article_gets_no_ads(rf, _article_ads):
    assert slots(article(rf, paragraphs(4))) == 0
    assert AdEvent.objects.count() == 0


def test_broken_html_gets_no_ads_and_is_returned_as_is(rf, _article_ads):
    broken = paragraphs(8) + "<p>unclosed"

    assert article(rf, broken) == broken


def test_the_cached_render_is_never_modified(rf, _article_ads):
    body = paragraphs(10) + "<h2>Heading</h2>" + paragraphs(2)
    rendered = render_article(body)
    before = str(rendered.html)

    article(rf, rendered.html)

    assert str(rendered.html) == before


@pytest.mark.parametrize("why", ["consent", "ads_off", "placement_off", "excluded"])
def test_nothing_renders_when_ads_are_not_allowed(
    rf, article_ads, ads_on, monkeypatch, why
):
    placement, _creatives = article_ads
    if why == "consent":
        monkeypatch.setattr(zone_service, "has_consent", lambda r, c: False)
    elif why == "ads_off":
        ads_on.ads_enabled = False
        ads_on.save()
    elif why == "placement_off":
        placement.is_enabled = False
        placement.save()
    else:
        TemplateAdExclusion.objects.create(template_path=POST_PAGE)
    source = paragraphs(10)

    assert article(rf, source) == source
    assert AdEvent.objects.count() == 0


def test_premium_and_noads_users_see_no_slots(
    rf, _article_ads, django_user_model, monkeypatch
):
    member = django_user_model.objects.create_user(
        username="premium", email="premium@example.test"
    )
    source = paragraphs(10)
    assert slots(article(rf, source, user=member)) == 2

    monkeypatch.setattr("apps.ads.rewarded.user_is_ad_free", lambda user: True)

    assert article(rf, source, user=member) == source
    assert AdEvent.objects.filter(event_type="impression").count() == 2  # none new


def test_excluded_roles_see_no_slots(rf, _article_ads, ads_on, django_user_model):
    staff = django_user_model.objects.create_user(
        username="staffer", email="staffer@example.test", is_staff=True
    )
    ads_on.excluded_user_roles = "staff,superuser"
    ads_on.save()
    source = paragraphs(10)

    assert article(rf, source, user=staff) == source


@pytest.mark.parametrize(
    "page", ["shop/pricing.html", "shop/checkout.html", "wallet/dashboard.html"]
)
def test_the_checkout_and_account_deny_list_beats_a_site_wide_placement(
    rf, campaign, page
):
    make_placement("in_article", "", [make_creative(campaign, "wide")])
    source = paragraphs(10)

    assert article(rf, source, page=page) == source


@pytest.mark.parametrize(
    "page", ["errors/404.html", "account/login.html", "consent/x.html"]
)
def test_error_and_sign_in_pages_never_carry_ads(rf, campaign, page):
    make_placement("in_article", page, [make_creative(campaign, "err")])
    source = paragraphs(10)

    assert article(rf, source, page=page) == source


def test_a_mobile_only_placement_is_skipped_on_desktop(rf, campaign):
    make_placement(
        "in_article", POST_PAGE, [make_creative(campaign, "m")], device_target="mobile"
    )
    source = paragraphs(10)

    assert article(rf, source) == source


def test_the_staff_test_profile_marks_slots_and_records_nothing(
    rf, _article_ads, django_user_model, _ads_on
):
    staff = django_user_model.objects.create_user(
        username="previewer", email="previewer@example.test", is_staff=True
    )
    staff.user_permissions.add(Permission.objects.get(codename="change_adplacement"))
    request = rf.get("/x/", HTTP_USER_AGENT=DESKTOP)
    request.user = staff
    request.session = {zone_service.PREVIEW_SESSION_KEY: 9_999_999_999}
    template = Template("{% load ad_tags %}{% ad_article_body html %}", name=POST_PAGE)

    html = template.render(
        Context({"request": request, "html": mark_safe(paragraphs(10))})
    )

    assert html.count('data-ad-zone="in_article"') == 2
    assert html.count('data-ad-test="test"') == 4  # the box and its label
    assert "TEST AD" in html
    assert AdEvent.objects.count() == 0


# ------------------------------------------------------------------- tiles ----


class _Tiles(HTMLParser):
    """Class attribute of every ``article`` and ``li`` in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"article", "li"}:
            self.items.append((tag, dict(attrs).get("class", "")))


def tiles_of(html: str) -> list[tuple[str, str]]:
    parser = _Tiles()
    parser.feed(html)
    return parser.items


def tile(
    rf, position: int, total: int, *, layout="tile", cls="", page=BRAND_PAGE, **kw
):
    return render(
        rf,
        "{% load ad_tags %}"
        f'{{% ad_tile {position} {total} layout="{layout}" tile="{cls}" page="{page}" %}}',
        page="x.html",
        **kw,
    )


@pytest.fixture(name="tile_ads")
def tile_ads_fixture(campaign):
    creatives = [make_creative(campaign, "t1"), make_creative(campaign, "t2")]
    return make_placement("between_items", BRAND_PAGE, creatives), creatives


@pytest.mark.parametrize(
    ("position", "total", "shown"),
    [
        (8, 9, True),
        (8, 8, False),
        (8, 7, False),
        (7, 20, False),
        (16, 20, True),
        (16, 17, True),
        (16, 16, False),
        (24, 40, False),
    ],
)
def test_tile_ads_follow_the_count_aware_positions(
    rf, _tile_ads, position, total, shown
):
    html = tile(rf, position, total, cls="card-ui--tile card-ui--brand-tile")

    assert bool(html.strip()) is shown


def test_a_tile_ad_has_exactly_the_siblings_classes_plus_the_sponsored_accent(
    rf, _tile_ads
):
    html = tile(rf, 8, 12, cls="card-ui--tile card-ui--brand-tile")

    elements = tiles_of(html)
    assert len(elements) == 1
    element = elements[0]
    assert element == (
        "article",
        "card-ui card-ui--tile card-ui--brand-tile card-ui--sponsored",
    )
    assert "Sponsored" in html
    assert "Headline t1" in html or "Headline t2" in html
    assert 'rel="sponsored noopener nofollow"' in html
    assert 'target="_blank"' in html
    assert LANDING not in html  # click goes through the tracking redirect
    assert 'loading="lazy"' in html
    assert 'width="52" height="52"' in html
    assert "<script" not in html and "style=" not in html


def test_card_and_tag_layouts_reuse_their_siblings_structure(rf, _tile_ads):
    card = tile(rf, 8, 12, layout="card", cls="card-ui--device")
    firmware = tile(rf, 8, 12, layout="card", cls="card-ui--firmware fw-card")
    tag = tile(rf, 8, 12, layout="tag")

    assert tiles_of(card)[0] == (
        "article",
        "card-ui card-ui--device card-ui--sponsored",
    )
    assert (
        tiles_of(firmware)[0][1]
        == "card-ui card-ui--firmware fw-card card-ui--sponsored"
    )
    assert 'class="card-ui__body"' in card and 'class="card-ui__footer"' in card
    assert tiles_of(tag)[0] == ("li", "tag-card tag-card--sponsored")


def test_a_tile_ad_never_shows_internal_creative_names(rf, campaign):
    make_placement(
        "between_items",
        BRAND_PAGE,
        [
            make_creative(
                campaign,
                "Internal Q1 Banner",
                headline="",
                tagline="",
                image_url="https://cdn.example/banner.png",
            )
        ],
    )

    html = tile(rf, 8, 12, cls="card-ui--tile")

    assert "Internal Q1 Banner" not in html
    assert "Visit advertiser.example" in html


def test_native_creatives_are_shown_inside_the_tile(rf, campaign):
    make_placement(
        "between_items",
        BRAND_PAGE,
        [
            make_creative(
                campaign,
                "native",
                creative_type="native",
                html="<p>Native copy</p>",
            )
        ],
    )

    html = tile(rf, 8, 12, cls="card-ui--tile")

    assert (
        '<span class="ad-tile__label">Sponsored</span><div class="ad-tile__raw"><p>Native copy</p></div>'
        in html
    )


def test_a_listing_asks_the_database_once_not_once_per_tile(rf, _tile_ads):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    request = rf.get("/x/", HTTP_USER_AGENT=DESKTOP)
    request.user = AnonymousUser()
    source = "{% load ad_tags %}" + "".join(
        f'{{% ad_tile {n} 40 tile="card-ui--tile" page="{BRAND_PAGE}" %}}'
        for n in range(1, 41)
    )
    template = Template(source, name="x.html")
    template.render(Context({"request": request}))  # warm the per-request state

    with CaptureQueriesContext(connection) as queries:
        template.render(Context({"request": request}))

    assert len(queries) <= 8, [q["sql"][:80] for q in queries]


def test_tile_slots_are_limited_by_the_placement_maximum_and_page_budget(
    rf, _tile_ads, _ads_on
):
    request = rf.get("/x/", HTTP_USER_AGENT=DESKTOP)
    request.user = AnonymousUser()
    source = "{% load ad_tags %}" + "".join(
        f'{{% ad_tile {n} 40 tile="card-ui--tile" page="{BRAND_PAGE}" %}}'
        for n in (8, 16, 24, 32)
    )

    html = Template(source, name="x.html").render(Context({"request": request}))

    assert html.count("data-ad-tile") == 2  # placement maximum


def test_a_page_budget_of_one_allows_one_tile_ad(rf, _tile_ads, ads_on):
    ads_on.max_ads_per_page = 1
    ads_on.save()
    request = rf.get("/x/", HTTP_USER_AGENT=DESKTOP)
    request.user = AnonymousUser()
    source = "{% load ad_tags %}" + "".join(
        f'{{% ad_tile {n} 40 tile="card-ui--tile" page="{BRAND_PAGE}" %}}'
        for n in (8, 16)
    )

    html = Template(source, name="x.html").render(Context({"request": request}))

    assert html.count("data-ad-tile") == 1


def test_tile_ads_are_suppressed_like_every_other_zone(
    rf, _tile_ads, _ads_on, monkeypatch
):
    monkeypatch.setattr(zone_service, "has_consent", lambda r, c: False)
    assert tile(rf, 8, 12, cls="card-ui--tile").strip() == ""
    monkeypatch.setattr(zone_service, "has_consent", lambda r, c: True)
    monkeypatch.setattr("apps.ads.rewarded.user_is_ad_free", lambda user: True)
    member = type(
        "U",
        (),
        {"is_authenticated": True, "is_staff": False, "is_superuser": False, "pk": 1},
    )()
    assert tile(rf, 8, 12, cls="card-ui--tile", user=member).strip() == ""
    monkeypatch.undo()


@pytest.mark.parametrize("page", ["errors/404.html", "shop/checkout.html"])
def test_tile_ads_are_never_placed_on_error_or_checkout_pages(rf, campaign, page):
    make_placement(
        "between_items",
        "" if page.startswith("shop") else page,
        [make_creative(campaign, "e")],
    )

    assert tile(rf, 8, 12, cls="card-ui--tile", page=page).strip() == ""


def test_tile_placement_numbers_tune_first_every_max_and_minimum(rf, campaign):
    make_placement(
        "between_items",
        BRAND_PAGE,
        [make_creative(campaign, "n")],
        auto_first_after=4,
        auto_every=4,
        auto_max=1,
        auto_min_items=5,
    )

    assert tile(rf, 4, 6, cls="card-ui--tile").strip()
    assert not tile(rf, 8, 12, cls="card-ui--tile").strip()
    assert not tile(rf, 4, 4, cls="card-ui--tile").strip()


# --------------------------------------------------------- real listing pages ----


def sponsored(html: str) -> list[int]:
    """Indexes (among all tiles) of the sponsored articles."""
    items = [
        cls for tag, cls in tiles_of(html) if tag == "article" and "card-ui" in cls
    ]
    return [i for i, cls in enumerate(items) if "card-ui--sponsored" in cls]


def make_brands(count: int, prefix: str = "Alpha"):
    from apps.firmwares.models import Brand

    return [
        Brand.objects.create(name=f"{prefix} {i:02d}", slug=f"{prefix.lower()}-{i:02d}")
        for i in range(1, count + 1)
    ]


def test_brand_list_gets_a_same_class_ad_tile_after_the_eighth_tile(client, _tile_ads):
    make_brands(12)

    response = client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP)

    html = response.content.decode()
    assert response.status_code == 200
    assert sponsored(html) == [8]
    classes = {
        cls
        for tag, cls in tiles_of(html)
        if tag == "article" and "card-ui--brand-tile" in cls
    }
    assert classes == {
        "card-ui card-ui--tile card-ui--brand-tile",
        "card-ui card-ui--tile card-ui--brand-tile card-ui--sponsored",
    }


def test_journal_feed_gets_an_in_feed_sponsored_card(
    client, campaign, django_user_model
):
    placement = make_placement(
        "between_items",
        "blog/post_list.html",
        [make_creative(campaign, "journal")],
    )
    placement.auto_first_after = 8
    placement.auto_every = 8
    placement.save(update_fields=["auto_first_after", "auto_every"])
    author = django_user_model.objects.create_user(
        username="ad-journal-author", email="ad-journal-author@example.test"
    )
    published = timezone.now()
    for index in range(10):
        Post.objects.create(
            title=f"Journal article {index}",
            body="<p>A public article body long enough to render.</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=published,
            published_at=published,
        )

    response = client.get(reverse("blog:post_list"), HTTP_USER_AGENT=DESKTOP)

    assert response.status_code == 200
    html = response.content.decode()
    post_cards = list(re.finditer('class="post-card post-card--free"', html))
    assert len(post_cards) == 9
    ad_position = html.index("data-ad-tile")
    assert post_cards[7].start() < ad_position < post_cards[8].start()
    assert html.count("data-ad-tile") == 1


def test_guide_request_feed_uses_a_list_item_sponsored_tile(
    client, campaign, django_user_model
):
    make_placement(
        "between_items",
        "blog/requests/list.html",
        [make_creative(campaign, "guides")],
    )
    requester = django_user_model.objects.create_user(
        username="ad-guide-requester", email="ad-guide-requester@example.test"
    )
    for index in range(10):
        GuideRequest.objects.create(
            requester=requester,
            title=f"Approved guide request {index}",
            status=GuideRequest.Status.APPROVED,
        )

    response = client.get(reverse("blog:request_list"), HTTP_USER_AGENT=DESKTOP)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="guide-card guide-card--sponsored"' in html
    assert html.count("data-ad-tile") == 1


def test_brand_list_orphan_rule_and_minimum(client, _tile_ads):
    make_brands(8)
    assert (
        sponsored(
            client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()
        )
        == []
    )

    make_brands(1, prefix="Alpha9")
    html = client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()
    assert sponsored(html) == [8]
    items = [
        cls for tag, cls in tiles_of(html) if tag == "article" and "card-ui" in cls
    ]
    assert "card-ui--sponsored" not in items[-1]


def test_two_letter_sections_do_not_repeat_the_same_creative(client, campaign):
    make_placement("between_items", BRAND_PAGE, [make_creative(campaign, "only")])
    make_brands(10, "Alpha")
    make_brands(10, "Beta")

    html = client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()

    assert len(sponsored(html)) == 1


def test_no_ads_on_the_brand_list_without_a_placement_or_consent(
    client, campaign, monkeypatch
):
    make_brands(12)
    assert (
        sponsored(
            client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()
        )
        == []
    )

    make_placement("between_items", BRAND_PAGE, [make_creative(campaign, "x")])
    monkeypatch.setattr(zone_service, "has_consent", lambda r, c: False)
    assert (
        sponsored(
            client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()
        )
        == []
    )


def test_the_htmx_fragment_resolves_like_the_full_page(client, _tile_ads):
    make_brands(12)

    html = client.get(
        "/firmwares/brands/", HTTP_HX_REQUEST="true", HTTP_USER_AGENT=DESKTOP
    ).content.decode()

    assert "card-ui--sponsored" in html or sponsored(html) == [8]


def test_paginated_model_lists_count_per_page(client, campaign):
    from apps.firmwares.models import Brand, Model

    brand = Brand.objects.create(name="Pager", slug="pager")
    for i in range(30):
        Model.objects.create(brand=brand, name=f"Phone {i:02d}", slug=f"phone-{i:02d}")
    make_placement(
        "between_items",
        "firmwares/brand_detail.html",
        [make_creative(campaign, "m1"), make_creative(campaign, "m2")],
    )

    first = client.get("/firmwares/pager/", HTTP_USER_AGENT=DESKTOP).content.decode()
    second = client.get(
        "/firmwares/pager/?page=2", HTTP_USER_AGENT=DESKTOP
    ).content.decode()

    assert sponsored(first) == [8, 17]  # after real tiles 8 and 16; a tile follows each
    assert sponsored(second) == []  # six tiles: below the minimum
    assert "card-ui card-ui--device card-ui--sponsored" in first


def test_empty_listings_render_no_ad(client, _tile_ads):
    html = client.get("/firmwares/brands/", HTTP_USER_AGENT=DESKTOP).content.decode()

    assert "data-ad-tile" not in html


# -------------------------------------------------------- scanner and CSS ----


@pytest.mark.parametrize(
    ("page", "zone"),
    [
        (POST_PAGE, "in_article"),
        ("firmwares/brand_list.html", "between_items"),
        ("firmwares/chipset_list.html", "between_items"),
        ("firmwares/brand_detail.html", "between_items"),
        ("firmwares/chipset_detail.html", "between_items"),
        ("firmwares/browse.html", "between_items"),
        ("tags/list.html", "between_items"),
        ("firmwares/library_hub.html", "between_items"),
        ("blog/post_list.html", "between_items"),
        ("blog/archive.html", "between_items"),
        ("blog/brand_posts.html", "between_items"),
        ("blog/model_posts.html", "between_items"),
        ("blog/chipset_posts.html", "between_items"),
        ("blog/requests/list.html", "between_items"),
        ("firmwares/model_detail.html", "between_items"),
        ("firmwares/flashing_guides.html", "between_items"),
        ("firmwares/flashing_category.html", "between_items"),
    ],
)
def test_the_placement_manager_detects_the_automatic_zones(page, zone):
    assert zone in placement_manager.declared_zones(page)


def test_an_automatic_zone_can_be_placed_from_the_manager(django_user_model):
    user = django_user_model.objects.create_user(
        username="mgr", email="mgr@example.test"
    )

    placement = placement_manager.place_ad(
        user=user, template_path=POST_PAGE, zone_code="in_article", enabled=False
    )

    assert (placement.zone, placement.template_reference, placement.is_enabled) == (
        "in_article",
        POST_PAGE,
        False,
    )
    assert (placement.auto_first_after, placement.auto_every) == (0, 0)


def test_the_console_edits_the_spacing_and_the_tile_text(
    client, django_user_model, article_ads
):
    admin = django_user_model.objects.create_superuser(
        username="ads-root", email="ads-root@example.test"
    )
    client.force_login(admin)
    placement, creatives = article_ads

    page = client.get(reverse("admin:ads_adplacement_change", args=[placement.pk]))
    creative = client.get(
        reverse("admin:ads_adcreative_change", args=[creatives[0].pk])
    )

    assert page.status_code == creative.status_code == 200
    for name in ("auto_first_after", "auto_every", "auto_max", "auto_min_items"):
        assert f'name="{name}"' in page.content.decode()
    assert "Automatic spacing" in page.content.decode()
    for name in ("headline", "tagline"):
        assert f'name="{name}"' in creative.content.decode()


ADS_CSS = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "styles" / "ads.css"
)
BOX_PROPERTIES = (
    "width",
    "height",
    "padding",
    "margin",
    "min-height",
    "aspect-ratio",
    "box-shadow",
    "border-radius",
    "border-width",
    "display",
    "grid",
    "flex",
    "gap",
)


def test_sponsored_selectors_never_change_the_tile_box():
    css = ADS_CSS.read_text(encoding="utf-8")
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    checked = 0
    for selector, body in rules:
        if "sponsored" not in selector and ".ad-tile__label" not in selector:
            continue
        if " img" in selector:  # sizes the picture inside the tile, not the tile
            continue
        checked += 1
        declared = {
            line.split(":")[0].strip() for line in body.split(";") if ":" in line
        }
        assert not declared & set(BOX_PROPERTIES), (selector.strip(), declared)
    assert checked >= 2
