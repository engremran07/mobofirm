"""Every firmware list surface answers ``q`` through the unified search engine."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.firmwares.models import (
    Brand,
    Chipset,
    EngineeringFirmware,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
    OfficialFirmware,
)

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Suggestion vocabularies are cached; start every test empty."""
    cache.clear()


@pytest.fixture(name="world")
def _world() -> dict[str, object]:
    """Two brands, two chipsets, devices, firmware, a tool and a guide."""
    samsung = Brand.objects.create(name="Samsung", slug="samsung")
    xiaomi = Brand.objects.create(name="Xiaomi", slug="xiaomi")
    d9200 = Chipset.objects.create(
        name="Dimensity 9200",
        slug="mediatek-dimensity-9200",
        vendor="MediaTek",
        aliases=["MT6985"],
        family="Dimensity",
    )
    sd8 = Chipset.objects.create(
        name="Snapdragon 8 Gen 2",
        slug="qualcomm-snapdragon-8-gen-2",
        vendor="Qualcomm",
        family="Snapdragon",
    )
    galaxy = Model.objects.create(
        brand=samsung,
        name="Galaxy S23",
        slug="galaxy-s23",
        model_code="SM-S911B",
        chipset_ref=sd8,
    )
    pixel_like = Model.objects.create(
        brand=xiaomi,
        name="Redmi Note 12",
        slug="redmi-note-12",
        model_code="23021RAAEG",
        chipset_ref=d9200,
    )
    Model.objects.create(
        brand=xiaomi,
        name="Hidden Phone",
        slug="hidden-phone",
        model_code="HID-1",
        is_active=False,
    )
    official = OfficialFirmware.objects.create(
        original_file_name="S911BXXU1AWB1.zip",
        stored_file_path="/firmware/s911.zip",
        brand=samsung,
        model=galaxy,
        is_verified=True,
    )
    engineering = EngineeringFirmware.objects.create(
        original_file_name="S911B-engineering-build.zip",
        stored_file_path="/firmware/s911-eng.zip",
        brand=samsung,
        model=galaxy,
        is_verified=True,
    )
    pending = OfficialFirmware.objects.create(
        original_file_name="S911B-secret-pending.zip",
        stored_file_path="/firmware/s911-secret.zip",
        brand=samsung,
        model=galaxy,
        is_verified=False,
    )
    category = FlashingToolCategory.objects.create(name="Samsung tools", slug="sam")
    odin = FlashingTool.objects.create(category=category, name="Odin", slug="odin")
    FlashingTool.objects.create(category=category, name="Heimdall", slug="heimdall")
    return {
        "samsung": samsung,
        "xiaomi": xiaomi,
        "d9200": d9200,
        "sd8": sd8,
        "galaxy": galaxy,
        "redmi": pixel_like,
        "official": official,
        "engineering": engineering,
        "pending": pending,
        "odin": odin,
    }


def _publish_guide(world: dict[str, object], title: str, published: bool) -> None:
    slug = title.lower().replace(" ", "-")
    author = get_user_model().objects.create_user(
        username=slug, email=f"{slug}@example.test"
    )
    post = Post.objects.create(
        title=title, body="<p>Guide</p>", author=author, firmware_brand=world["samsung"]
    )
    status = PostStatus.PUBLISHED if published else PostStatus.DRAFT
    Post.objects.filter(pk=post.pk).update(
        status=status,
        is_published=published,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )
    template = FlashingGuideTemplate.objects.create(
        title_template=title,
        body_template="Body",
        guide_type=FlashingGuideTemplate.GuideType.STOCK_FLASH,
        brand=world["samsung"],
    )
    GeneratedFlashingGuide.objects.create(
        template=template,
        post=post,
        brand=world["samsung"],
        model=world["galaxy"],
    )


def _get(
    name: str,
    params: dict[str, str],
    *,
    hx: bool = False,
    kwargs: dict[str, str] | None = None,
):
    headers = HX if hx else {}
    return Client().get(reverse(name, kwargs=kwargs), params, headers=headers)


@pytest.mark.usefixtures("world")
def test_browse_search_covers_every_classification_and_hides_unapproved() -> None:
    """q finds official and engineering files, never pending ones."""
    response = _get("firmwares:browse", {"q": "S911B"})
    names = {firmware.original_file_name for firmware in response.context["firmwares"]}
    assert names == {"S911BXXU1AWB1.zip", "S911B-engineering-build.zip"}
    assert b"secret-pending" not in response.content


@pytest.mark.usefixtures("world")
def test_browse_search_keeps_other_filters_and_types() -> None:
    """q combines with the type and brand filters."""
    typed = _get("firmwares:browse", {"q": "S911B", "type": "engineering"})
    assert [f.original_file_name for f in typed.context["firmwares"]] == [
        "S911B-engineering-build.zip"
    ]
    other_brand = _get("firmwares:browse", {"q": "S911B", "brand": "xiaomi"})
    assert not list(other_brand.context["firmwares"])


@pytest.mark.usefixtures("world")
def test_browse_is_typo_tolerant_and_offers_did_you_mean() -> None:
    """A misspelt brand still finds files and is corrected."""
    response = _get("firmwares:browse", {"q": "samung"})
    assert response.context["search_corrected"] == "samsung"
    assert len(list(response.context["firmwares"])) == 2
    assert [s.title for s in response.context["did_you_mean"]] == ["Samsung"]


@pytest.mark.usefixtures("world")
def test_browse_htmx_gets_table_fragment_and_live_trigger_is_wired() -> None:
    """Typing triggers HTMX (not just blur) and the partial has no layout."""
    full = _get("firmwares:browse", {"q": "S911B"})
    assert b'hx-trigger="input changed delay:250ms' in full.content
    fragment = _get("firmwares:browse", {"q": "S911B"}, hx=True)
    assert b"<html" not in fragment.content
    assert b"S911BXXU1AWB1" in fragment.content
    assert "HX-Request" in fragment["Vary"]


@pytest.mark.usefixtures("world")
def test_browse_empty_state_keeps_request_buttons() -> None:
    """No match: suggestions plus the request-a-firmware/model buttons."""
    response = _get("firmwares:browse", {"q": "zzzqqq"})
    assert b'data-request-cta="firmware"' in response.content
    assert b'data-request-cta="model"' in response.content


@pytest.mark.usefixtures("world")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("SM-S911B", ["Galaxy S23"]),
        ("galaxy", ["Galaxy S23"]),
        ("snapdragon", ["Galaxy S23"]),
        ("MT6985", ["Redmi Note 12"]),
        ("galxy", ["Galaxy S23"]),
        ("xiaomi", ["Redmi Note 12"]),
    ],
)
def test_device_search_matches_codes_names_chipsets_and_typos(
    query: str, expected: list[str]
) -> None:
    """The /firmwares/search/ endpoint uses the shared engine."""
    response = _get("firmwares:universal_search", {"q": query})
    assert [device.name for device in response.context["results"]] == expected


@pytest.mark.usefixtures("world")
def test_device_search_never_returns_inactive_models() -> None:
    """Inactive devices stay hidden."""
    response = _get("firmwares:universal_search", {"q": "Hidden"})
    assert not response.context["results"]
    assert b"Hidden Phone" not in response.content


@pytest.mark.usefixtures("world")
def test_device_search_htmx_fragment_has_feedback_and_suggestions() -> None:
    """The fragment shows the count and a typed 'did you mean'."""
    response = _get("firmwares:universal_search", {"q": "samung"}, hx=True)
    assert b"<html" not in response.content
    assert b"data-did-you-mean" in response.content
    assert b"(brand)" in response.content


@pytest.mark.usefixtures("world")
def test_brand_list_search_filters_and_returns_fragment() -> None:
    """Brands filter server-side and live."""
    response = _get("firmwares:brand_list", {"q": "xiao"})
    assert [b.name for b in response.context["brands"]] == ["Xiaomi"]
    fragment = _get("firmwares:brand_list", {"q": "xiao"}, hx=True)
    assert b"<html" not in fragment.content
    assert b"Xiaomi" in fragment.content
    assert b"Samsung" not in fragment.content
    typo = _get("firmwares:brand_list", {"q": "samung"})
    assert [b.name for b in typo.context["brands"]] == ["Samsung"]


@pytest.mark.usefixtures("world")
def test_library_hub_brand_search_uses_the_engine() -> None:
    """The hub's brand fragment is typo tolerant."""
    response = _get("firmwares:library_hub", {"q": "xiomi"}, hx=True)
    assert b"Xiaomi" in response.content


def test_brand_detail_filters_its_models(world: dict[str, object]) -> None:
    """Brand pages search only that brand's active models."""
    Model.objects.create(brand=world["samsung"], name="Galaxy A54", slug="galaxy-a54")
    page = _get("firmwares:brand_detail", {"q": "A54"}, kwargs={"slug": "samsung"})
    assert [m.name for m in page.context["models"]] == ["Galaxy A54"]
    assert page.context["model_total"] == 2
    fragment = _get(
        "firmwares:brand_detail", {"q": "A54"}, hx=True, kwargs={"slug": "samsung"}
    )
    assert b"<html" not in fragment.content
    assert b"Galaxy A54" in fragment.content
    assert b"Galaxy S23" not in fragment.content
    other = _get(
        "firmwares:brand_detail", {"q": "Redmi"}, hx=True, kwargs={"slug": "samsung"}
    )
    assert b"Redmi Note 12" not in other.content
    assert b'data-request-cta="model"' in other.content


def test_chipset_detail_filters_its_devices(world: dict[str, object]) -> None:
    """Chipset pages search the devices that use the chipset."""
    chip = world["sd8"]
    page = _get("firmwares:chipset_detail", {"q": "galaxy"}, kwargs={"slug": chip.slug})
    assert [m.name for m in page.context["models"]] == ["Galaxy S23"]
    none = _get("firmwares:chipset_detail", {"q": "redmi"}, kwargs={"slug": chip.slug})
    assert not list(none.context["models"])


@pytest.mark.usefixtures("world")
def test_tools_search_filters_tools_and_hides_guides_while_searching() -> None:
    """Tools search by name, typo tolerant, with an HTMX fragment."""
    page = _get("firmwares:flashing_tools", {"q": "odin"})
    assert page.context["tool_total"] == 1
    typo = _get("firmwares:flashing_tools", {"q": "heimdaal"})
    assert typo.context["search_corrected"] == "heimdall"
    fragment = _get("firmwares:flashing_tools", {"q": "odin"}, hx=True)
    assert b"<html" not in fragment.content
    assert b"Odin" in fragment.content
    assert b"Heimdall" not in fragment.content


@pytest.mark.usefixtures("world")
def test_tools_empty_search_keeps_request_button() -> None:
    """An empty tool search keeps the request button."""
    response = _get("firmwares:flashing_tools", {"q": "zzzzqq"}, hx=True)
    assert b'data-request-cta="chipset"' in response.content


def test_guides_search_hides_drafts(world: dict[str, object]) -> None:
    """Guide search lists published guides only."""
    _publish_guide(world, "Flash Galaxy S23 stock", published=True)
    _publish_guide(world, "Flash Galaxy S23 secret draft", published=False)
    page = _get("firmwares:flashing_guides", {"q": "galaxy"})
    titles = [guide.post.title for guide in page.context["guides"]]
    assert titles == ["Flash Galaxy S23 stock"]
    fragment = _get("firmwares:flashing_guides", {"q": "galaxy"}, hx=True)
    assert b"<html" not in fragment.content
    assert b"secret draft" not in fragment.content
    by_code = _get("firmwares:flashing_guides", {"q": "SM-S911B"})
    assert len(list(by_code.context["guides"])) == 1


@pytest.mark.usefixtures("world")
def test_legacy_autocomplete_json_uses_engine_and_hides_unapproved() -> None:
    """The JSON autocomplete no longer has its own icontains queries."""
    payload = _get("firmwares:api_search_autocomplete", {"q": "S911B"}).json()
    names = {item["name"] for item in payload["results"]}
    assert "S911BXXU1AWB1.zip" in names
    assert not any("pending" in name for name in names)
    chips = _get(
        "firmwares:api_search_autocomplete", {"q": "9200", "type": "chipsets"}
    ).json()
    assert [item["type"] for item in chips["results"]] == ["chipset"]
    assert _get("firmwares:api_search_autocomplete", {"q": "a"}).json()["results"] == []
