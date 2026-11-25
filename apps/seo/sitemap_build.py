"""Sitemap catalogue: sections -> partitions -> numbered files of verified entries.

One engine builds everything from the registry in :mod:`apps.seo.sitemap_registry`
(items already reconciled with the resolver's indexation policy). It never lists a URL
the resolver would not index, never invents a ``lastmod`` and only advertises images
that exist on this site and that Googlebot-Image may fetch.

File naming is predictable: ``sitemap-<section>[-<partition>]-<n>.xml`` where the
optional partition is a brand slug (models, firmware) or a chipset vendor slug
(chipsets) so each file stays small, topical and has its own accurate ``lastmod``.
Paging is by position with a configurable page size capped at the protocol limit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from django.conf import settings as django_settings
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.text import slugify

from apps.site_settings.services import get_site_settings

from . import robots
from .models import SEOSettings
from .policy import is_private_path
from .sitemap_registry import get_sitemaps
from .siteurls import site_origin

logger = logging.getLogger(__name__)

MAX_URLS = 50_000  # sitemaps.org: URLs per file
MAX_BYTES = 50 * 1024 * 1024  # sitemaps.org: uncompressed bytes per file
MAX_IMAGES_PER_URL = 10
NEWS_WINDOW = timedelta(hours=48)  # Google News: articles from the last two days
NEWS_LIMIT = 1000  # Google News: URLs per news sitemap
NEWS_NAME = "news"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif")
# Conventional attributes a sitemap item may expose when its class defines no
# ``images(item)`` hook. Values are URLs or Django file fields.
IMAGE_ATTRIBUTES = (
    "cover_image_url",
    "image_url",
    "logo_url",
    "image_upload",
    "logo_upload",
    "hero_image",
    "og_image",
)
IMAGE_AGENT = "Googlebot-Image"
# Sections split into one file series per partition; the value is the dotted attribute
# path of the item that names the partition.
PARTITION_PATHS: dict[str, str] = {
    "models": "brand.slug",
    "firmware": "brand.slug",
    "chipsets": "vendor",
}
OTHER_PARTITION = "other"

_partition_functions: dict[str, Callable[[Any], str]] = {}


def register_partition(section: str, key: Callable[[Any], str]) -> None:
    """Split ``section`` into one file series per ``key(item)`` (a slug-able string)."""
    _partition_functions[section] = key


@dataclass(frozen=True)
class Entry:
    loc: str
    lastmod: datetime | date | None = None
    item: Any = None
    alternates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NewsEntry:
    loc: str
    title: str
    published: datetime
    language: str


@dataclass
class SitemapFile:
    section: str
    partition: str
    page: int
    entries: list[Entry] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.section}-{self.partition}" if self.partition else self.section

    @property
    def name(self) -> str:
        return f"sitemap-{self.key}-{self.page}.xml"

    @property
    def path(self) -> str:
        return f"/{self.name}"

    @property
    def latest_lastmod(self) -> datetime | date | None:
        return latest(entry.lastmod for entry in self.entries)


def _aware(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_default_timezone())
        return value
    return timezone.make_aware(
        datetime(value.year, value.month, value.day), timezone.get_default_timezone()
    )


def clean_lastmod(value: object) -> datetime | date | None:
    """A real, non-future modification time; anything else is dropped."""
    if not isinstance(value, datetime | date):
        return None
    if _aware(value) > timezone.now() + timedelta(minutes=5):
        return None
    return value


def latest(values: Iterable[datetime | date | None]) -> datetime | date | None:
    known = [value for value in values if value is not None]
    return max(known, key=_aware) if known else None


def w3c(value: datetime | date) -> str:
    """W3C Datetime (UTC, second precision) for ``<lastmod>``."""
    if isinstance(value, datetime):
        return _aware(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def _dotted(item: Any, path: str) -> object:
    value: object = item
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


def partition_of(section: str, item: Any) -> str:
    key_function = _partition_functions.get(section)
    if key_function is not None:
        raw: object = key_function(item)
    elif section in PARTITION_PATHS:
        raw = _dotted(item, PARTITION_PATHS[section])
    else:
        return ""
    return slugify(str(raw or "")) or OTHER_PARTITION


def is_partitioned(section: str) -> bool:
    return section in _partition_functions or section in PARTITION_PATHS


def page_size(row: SEOSettings) -> int:
    return max(1, min(int(row.sitemap_page_size or MAX_URLS), MAX_URLS))


def _value(sitemap: Any, name: str, item: Any) -> object:
    attribute = getattr(sitemap, name, None)
    return attribute(item) if callable(attribute) else attribute


def _absolute(location: str, origin: str) -> str:
    if urlsplit(location).scheme in {"http", "https"}:
        return location
    return urljoin(f"{origin}/", location) if location.startswith("/") else ""


def section_entries(section: str, origin: str) -> list[Entry]:
    """Every listed entry of a section, already filtered by indexation policy."""
    sitemap_class = get_sitemaps().get(section)
    if sitemap_class is None:
        return []
    sitemap = sitemap_class()
    entries: list[Entry] = []
    seen: set[str] = set()
    for item in sitemap.items():
        loc = _absolute(str(sitemap.location(item)), origin)
        if not loc or loc in seen:
            continue
        seen.add(loc)
        declared = _value(sitemap, "alternates", item)
        alternates = (
            tuple(sorted((str(lang), str(url)) for lang, url in declared.items()))
            if isinstance(declared, dict) and len(declared) > 1
            else ()
        )
        entries.append(
            Entry(
                loc=loc,
                lastmod=clean_lastmod(_value(sitemap, "lastmod", item)),
                item=item,
                alternates=alternates,
            )
        )
    return entries


def section_files(
    section: str, row: SEOSettings, origin: str, entries: list[Entry] | None = None
) -> list[SitemapFile]:
    """Numbered files of one section, one series per partition."""
    listed = section_entries(section, origin) if entries is None else entries
    groups: dict[str, list[Entry]] = {}
    for entry in listed:
        groups.setdefault(partition_of(section, entry.item), []).append(entry)
    size = page_size(row)
    files: list[SitemapFile] = []
    for partition in sorted(groups):
        chunk = groups[partition]
        for number, start in enumerate(range(0, len(chunk), size), start=1):
            files.append(
                SitemapFile(section, partition, number, chunk[start : start + size])
            )
    return files


def catalog(row: SEOSettings, origin: str) -> list[SitemapFile]:
    """Every non-empty file of every section, in registry order."""
    files: list[SitemapFile] = []
    for section in get_sitemaps():
        files.extend(section_files(section, row, origin))
    return files


def locate(key: str, sections: Iterable[str]) -> tuple[str, str] | None:
    """Split a file key into ``(section, partition)`` using the registered names."""
    matches = [name for name in sections if key == name or key.startswith(f"{name}-")]
    if not matches:
        return None
    section = max(matches, key=len)
    return section, key[len(section) + 1 :]


# --- news -------------------------------------------------------------------------


def news_entries(row: SEOSettings, origin: str) -> list[NewsEntry]:
    """Fresh articles from sections that declare ``news(item)``; empty unless enabled.

    A section opts in by defining ``news(item) -> (title, published) | None``. Items
    still come from the policy-filtered listing, so noindex and non-canonical posts
    never appear. Only articles from the last 48 hours qualify.
    """
    if not (row.sitemap_news_enabled and row.seo_enabled and row.sitemap_enabled):
        return []
    language = (get_site_settings().default_language or "en").split("-")[0].lower()
    cutoff = timezone.now() - NEWS_WINDOW
    found: list[NewsEntry] = []
    for section, sitemap_class in get_sitemaps().items():
        sitemap = sitemap_class()
        news_hook = getattr(sitemap, "news", None)
        if not callable(news_hook):
            continue
        for entry in section_entries(section, origin):
            declared = news_hook(entry.item)
            if not declared:
                continue
            title, published = declared
            if (
                isinstance(published, datetime)
                and title
                and cutoff <= _aware(published) <= timezone.now()
            ):
                found.append(NewsEntry(entry.loc, str(title), published, language))
    found.sort(key=lambda news: _aware(news.published), reverse=True)
    return found[:NEWS_LIMIT]


# --- images -----------------------------------------------------------------------


def _media_prefix() -> str:
    return urljoin("/", str(django_settings.MEDIA_URL))


def _static_prefix() -> str:
    return urljoin("/", str(django_settings.STATIC_URL))


def _file_exists(path: str) -> bool:
    media, static = _media_prefix(), _static_prefix()
    try:
        if path.startswith(media):
            return default_storage.exists(unquote(path[len(media) :]))
        if path.startswith(static):
            return bool(finders.find(unquote(path[len(static) :])))
    except (SuspiciousFileOperation, OSError, ValueError):
        return False
    return False


def verified_image(
    value: object, origin: str, rules: robots.ParsedRobots
) -> str | None:
    """Absolute URL of a real, public, crawlable image on this site, else ``None``."""
    raw = getattr(value, "url", value) if getattr(value, "name", True) else ""
    if not isinstance(raw, str) or not raw:
        return None
    parts = urlsplit(raw)
    host = urlsplit(origin).netloc.lower()
    if parts.scheme in {"http", "https"}:
        if parts.netloc.lower() != host:
            return None
    elif parts.scheme or parts.netloc or not raw.startswith("/"):
        return None
    path = parts.path
    if (
        not path.lower().endswith(IMAGE_SUFFIXES)
        or is_private_path(path)
        or not rules.can_fetch(IMAGE_AGENT, path)
        or not _file_exists(path)
    ):
        return None
    return f"{origin}{path}"


def images_for(
    sitemap: Any, item: Any, origin: str, rules: robots.ParsedRobots
) -> list[str]:
    """Verified image URLs for one entry (hook ``images(item)`` or conventional attrs)."""
    hook = getattr(sitemap, "images", None)
    candidates: list[object] = (
        list(hook(item) or [])
        if callable(hook)
        else [getattr(item, name, None) for name in IMAGE_ATTRIBUTES]
    )
    found: list[str] = []
    for candidate in candidates:
        url = verified_image(candidate, origin, rules)
        if url and url not in found:
            found.append(url)
        if len(found) >= MAX_IMAGES_PER_URL:
            break
    return found


def attach_images(
    section: str, files: list[SitemapFile], origin: str, row: SEOSettings
) -> dict[str, list[str]]:
    """Image URLs per entry ``loc`` for the given files (empty when disabled)."""
    if not row.sitemap_images_enabled:
        return {}
    sitemap_class = get_sitemaps().get(section)
    if sitemap_class is None:
        return {}
    sitemap = sitemap_class()
    rules = robots.parse(robots.generate(row, origin))
    images: dict[str, list[str]] = {}
    for sitemap_file in files:
        for entry in sitemap_file.entries:
            urls = images_for(sitemap, entry.item, origin, rules)
            if urls:
                images[entry.loc] = urls
    return images


# --- validation ----------------------------------------------------------------------


def estimate_bytes(entry_count: int, image_count: int = 0) -> int:
    """Conservative uncompressed size: ~220 bytes per URL, ~110 per image."""
    return 260 + entry_count * 220 + image_count * 110


def origin_for(request: Any = None) -> str:
    return site_origin(request)
