"""Popular models / firmware / articles: analytics window, scope, cache, safety."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.core.models import SearchSettings
from apps.core.search import popular_entries, popular_settings
from apps.firmwares.models import (
    Brand,
    Chipset,
    DownloadToken,
    Model,
    OfficialFirmware,
)
from apps.firmwares.tracking_models import FirmwareView

pytestmark = pytest.mark.django_db

HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Popular lists are cached; start every test empty."""
    cache.clear()


def _file(name: str, brand: Brand, model: Model, **extra: Any) -> OfficialFirmware:
    values: dict[str, Any] = {
        "original_file_name": name,
        "stored_file_path": f"/firmware/{name}",
        "brand": brand,
        "model": model,
        "is_verified": True,
    }
    values.update(extra)
    return OfficialFirmware.objects.create(**values)


def _download(firmware: OfficialFirmware, *, days_ago: int = 0, count: int = 1) -> None:
    content_type = ContentType.objects.get_for_model(
        OfficialFirmware, for_concrete_model=False
    )
    when = timezone.now() - timedelta(days=days_ago)
    for _ in range(count):
        DownloadToken.objects.create(
            content_type=content_type,
            object_id=firmware.pk,
            status=DownloadToken.Status.USED,
            used_at=when,
            expires_at=when + timedelta(hours=1),
        )


def _view(firmware: OfficialFirmware, *, days_ago: int = 0, count: int = 1) -> None:
    content_type = ContentType.objects.get_for_model(
        OfficialFirmware, for_concrete_model=False
    )
    for index in range(count):
        view = FirmwareView.objects.create(
            content_type=content_type,
            object_id=firmware.pk,
            session_key=f"s{index}",
        )
        FirmwareView.objects.filter(pk=view.pk).update(
            viewed_at=timezone.now() - timedelta(days=days_ago)
        )


@pytest.fixture(name="world")
def _world() -> dict[str, Any]:
    """Two brands/chipsets with devices and files of different popularity."""
    samsung = Brand.objects.create(name="Samsung", slug="samsung")
    xiaomi = Brand.objects.create(name="Xiaomi", slug="xiaomi")
    chip = Chipset.objects.create(
        vendor="Qualcomm", family="Snapdragon", name="Snapdragon 8", slug="sd8"
    )
    galaxy = Model.objects.create(
        brand=samsung, name="Galaxy S23", slug="galaxy-s23", chipset_ref=chip
    )
    redmi = Model.objects.create(brand=xiaomi, name="Redmi 12", slug="redmi-12")
    hot = _file("hot-build.zip", samsung, galaxy)
    warm = _file("warm-build.zip", xiaomi, redmi)
    cold = _file("cold-build.zip", xiaomi, redmi)
    _download(hot, count=3)
    _view(hot, count=2)
    _download(warm, count=1)
    return {
        "samsung": samsung,
        "xiaomi": xiaomi,
        "chip": chip,
        "galaxy": galaxy,
        "redmi": redmi,
        "hot": hot,
        "warm": warm,
        "cold": cold,
    }


@pytest.mark.usefixtures("world")
def test_popular_firmware_ranks_by_downloads_and_views() -> None:
    """Downloads weigh more than views; files without activity never appear."""
    titles = [hit.title for hit in popular_entries("firmware")]
    assert titles == ["hot-build.zip", "warm-build.zip"]


@pytest.mark.usefixtures("world")
def test_popular_models_aggregate_their_files() -> None:
    """A model's popularity is the sum over its files."""
    hits = popular_entries("model")
    assert [hit.title for hit in hits] == ["Galaxy S23", "Redmi 12"]
    assert hits[0].url == reverse(
        "firmwares:model_detail", args=["samsung", "galaxy-s23"]
    )


def test_popular_window_is_configurable(world: dict[str, Any]) -> None:
    """Activity outside the window is ignored; the window is a staff setting."""
    _download(world["cold"], days_ago=20, count=9)
    assert "cold-build.zip" in [h.title for h in popular_entries("firmware")]
    SearchSettings.objects.create(popular_window_days=7)
    assert "cold-build.zip" not in [h.title for h in popular_entries("firmware")]
    assert popular_settings().window_days == 7


def test_popular_never_lists_unapproved_or_inactive_files(
    world: dict[str, Any],
) -> None:
    """Pending and inactive files are excluded however popular they are."""
    pending = _file(
        "pending-build.zip", world["samsung"], world["galaxy"], is_verified=False
    )
    gone = _file(
        "inactive-build.zip", world["samsung"], world["galaxy"], is_active=False
    )
    for item in (pending, gone):
        _download(item, count=50)
        _view(item, count=50)
    titles = [hit.title for hit in popular_entries("firmware")]
    assert "pending-build.zip" not in titles
    assert "inactive-build.zip" not in titles
    inactive_model = Model.objects.create(
        brand=world["samsung"], name="Hidden", slug="hidden", is_active=False
    )
    _download(_file("hidden-model.zip", world["samsung"], inactive_model), count=60)
    assert "Hidden" not in [hit.title for hit in popular_entries("model")]


@pytest.mark.usefixtures("world")
def test_popular_scope_by_brand_and_chipset() -> None:
    """Brand and chipset pages only see their own popular entries."""
    xiaomi = popular_entries("firmware", scope={"brand": "xiaomi"})
    assert [hit.title for hit in xiaomi] == ["warm-build.zip"]
    chip = popular_entries("firmware", scope={"chipset": "sd8"})
    assert [hit.title for hit in chip] == ["hot-build.zip"]
    assert [h.title for h in popular_entries("model", scope={"brand": "samsung"})] == [
        "Galaxy S23"
    ]


@pytest.mark.usefixtures("world")
def test_popular_is_cached_and_settings_change_invalidates() -> None:
    """A second call reads the cache; saving the settings drops it."""
    popular_entries("firmware")
    with CaptureQueriesContext(connection) as cached:
        popular_entries("firmware")
    assert len(cached) <= 2
    SearchSettings.objects.create(popular_limit=1)
    assert len(popular_entries("firmware")) == 1


def test_popular_cache_is_dropped_when_a_file_is_approved(
    world: dict[str, Any],
) -> None:
    """Approving a popular file refreshes the list without waiting for the TTL."""
    pending = _file("later.zip", world["samsung"], world["galaxy"], is_verified=False)
    _download(pending, count=40)
    assert "later.zip" not in [h.title for h in popular_entries("firmware")]
    pending.is_verified = True
    pending.save()
    assert popular_entries("firmware")[0].title == "later.zip"


@pytest.mark.usefixtures("world")
def test_popular_can_be_switched_off() -> None:
    """Staff can hide every popular block."""
    SearchSettings.objects.create(popular_enabled=False)
    assert popular_entries("firmware") == []
    assert popular_entries("model") == []


def test_popular_query_count_does_not_grow_with_data(world: dict[str, Any]) -> None:
    """No N+1: the same number of queries for 2 files and for 40."""
    with CaptureQueriesContext(connection) as small:
        popular_entries("firmware", limit=24)
        popular_entries("model", limit=24)
    cache.clear()
    for index in range(40):
        extra = _file(f"extra-{index}.zip", world["samsung"], world["galaxy"])
        _download(extra, count=2)
    with CaptureQueriesContext(connection) as large:
        popular_entries("firmware", limit=24)
        popular_entries("model", limit=24)
    assert len(large) <= len(small) + 2


def _publish(title: str, views: int, brand: Brand | None = None) -> Post:
    author = get_user_model().objects.create_user(
        username=title.lower().replace(" ", "-"), email=f"{len(title)}{views}@x.test"
    )
    post = Post.objects.create(
        title=title, body="<p>b</p>", author=author, firmware_brand=brand
    )
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
        views_count=views,
    )
    return post


def test_popular_articles_follow_reads_and_scope(world: dict[str, Any]) -> None:
    """Popular articles rank by reads and respect the brand scope."""
    _publish("Quiet article", 1)
    _publish("Samsung big read", 90, world["samsung"])
    assert popular_entries("blog")[0].title == "Samsung big read"
    scoped = popular_entries("blog", scope={"brand": "samsung"})
    assert [h.title for h in scoped] == ["Samsung big read"]


@pytest.mark.usefixtures("world")
def test_popular_blocks_load_lazily_on_catalogue_blog_and_search_pages() -> None:
    """Pages only carry a placeholder; the endpoint returns the blocks."""
    _publish("Hot journal read", 10)
    client = Client()
    pages = [
        reverse("firmwares:library_hub"),
        reverse("firmwares:browse"),
        reverse("firmwares:brand_detail", args=["samsung"]),
        reverse("firmwares:chipset_detail", args=["sd8"]),
        reverse("blog:post_list"),
        reverse("search:results"),
        reverse("search:results") + "?q=zzzzqqq",
    ]
    for url in pages:
        with CaptureQueriesContext(connection) as queries:
            html = client.get(url).content.decode()
        match = re.search(r'data-popular-slot hx-get="([^"]+)"', html)
        assert match, url
        assert "data-popular-blocks" not in html, url
        assert not [q for q in queries if "download_links_downloadtoken" in q["sql"]]
        fragment = client.get(match.group(1).replace("&amp;", "&"), headers=HX)
        body = fragment.content.decode()
        assert "data-popular-blocks" in body, url
        assert "<html" not in body
    everything = client.get(
        reverse("search:popular"), {"kinds": "model,firmware,blog"}
    ).content.decode()
    assert "Popular models" in everything
    assert "Popular firmware" in everything
    assert "Popular articles" in everything
    assert "Hot journal read" in everything
    assert "hot-build.zip" in everything


@pytest.mark.usefixtures("world")
def test_popular_endpoint_validates_parameters_and_escapes_heading() -> None:
    """Hostile kinds, scopes and headings are ignored or escaped."""
    client = Client()
    url = reverse("search:popular")
    html = client.get(
        url,
        {
            "kinds": "firmware,../x",
            "brand": "xiaomi",
            "heading": "<script>alert(1)</script>",
        },
    ).content.decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "warm-build.zip" in html
    assert "hot-build.zip" not in html
    ignored = client.get(
        url, {"kinds": "firmware", "brand": "x y;drop"}
    ).content.decode()
    assert "hot-build.zip" in ignored
    assert client.post(url).status_code == 405


def test_popular_blocks_hidden_when_nothing_is_popular() -> None:
    """No analytics, no block (no empty headings)."""
    html = (
        Client()
        .get(reverse("search:popular"), {"kinds": "model,firmware"})
        .content.decode()
    )
    assert "data-popular-blocks" not in html
