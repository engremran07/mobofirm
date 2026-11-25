"""Public syndication feeds: RSS 2.0, Atom 1.0 and JSON Feed 1.1.

What is in a feed is decided here and only here, from the same public rules as the rest
of the site: published, live blog posts and verified, active firmware. Content is read
through ``django.apps`` model lookup (no cross-app imports, like ``content``). Gated
posts and firmware carry a teaser and the page link, never the body or a download link;
drafts, scheduled and pending items never appear.

Feeds are deterministic for the same content (``lastBuildDate`` is the newest item
time, never "now"), so ETag/Last-Modified and 304 answers work. Output is built with
``xml.etree`` and therefore always well-formed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

from django.apps import apps
from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify

from apps.core.app_service import app_enabled
from apps.distribution import content as content_module
from apps.distribution.models import DistributionSettings
from apps.seo import sitemap_build
from apps.seo.siteurls import absolute_url
from apps.site_settings.services import get_site_settings

FORMATS = {
    "rss": ("application/rss+xml", "RSS 2.0"),
    "atom": ("application/atom+xml", "Atom 1.0"),
    "json": ("application/feed+json", "JSON Feed 1.1"),
}
FIRMWARE_TYPES = ("official", "engineering", "readback", "modified", "other")
NEWS_WINDOW = timedelta(hours=48)
TTL_MINUTES = 60
MAX_SLUG = 140
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

SITE_KEY = "latest"
BLOG_KEY = "section/blog"
FIRMWARE_KEY = "section/firmware"
NEWS_KEY = "google-news"
FIXED = (
    (SITE_KEY, "All updates", "New blog posts and verified firmware."),
    (BLOG_KEY, "Blog", "Published blog posts and guides."),
    (FIRMWARE_KEY, "Firmware", "Newly verified firmware (page links only)."),
    (
        NEWS_KEY,
        "Google News",
        "Full-text news posts of the last 48 hours for Google News and Discover.",
    ),
)


@dataclass(frozen=True, slots=True)
class FeedItem:
    ref: str
    title: str
    url: str
    summary: str
    html: str
    published: datetime
    updated: datetime
    author: str
    categories: tuple[str, ...] = ()
    image_url: str = ""
    kind: str = "blog"


@dataclass(frozen=True, slots=True)
class Spec:
    """A feed: ``key`` is the URL stem (``latest``, ``brand/acme``...)."""

    key: str
    title: str
    description: str
    scope: str = "site"  # site | blog | firmware | brand | chipset | tag | news
    slug: str = ""


@dataclass(frozen=True, slots=True)
class Built:
    spec: Spec
    fmt: str
    body: bytes
    content_type: str
    etag: str
    modified: datetime
    count: int


# ------------------------------------------------------------------ routing ----


def spec_for(key: str) -> Spec | None:
    """The feed a URL stem names, or ``None`` (unknown or invalid slug)."""
    fixed = {k: (t, d) for k, t, d in FIXED}
    if key in fixed:
        title, desc = fixed[key]
        scope = {
            SITE_KEY: "site",
            BLOG_KEY: "blog",
            FIRMWARE_KEY: "firmware",
            NEWS_KEY: "news",
        }[key]
        return Spec(key, f"{_site()}: {title}", desc, scope)
    scope, _sep, slug = key.partition("/")
    if (
        scope in {"brand", "chipset", "tag"}
        and _SLUG.match(slug)
        and len(slug) <= MAX_SLUG
    ):
        label = slug.replace("-", " ").title()
        return Spec(
            key,
            f"{_site()}: {label} ({scope})",
            f"Latest updates for the {scope} {label}.",
            scope,
            slug,
        )
    return None


def feed_url(key: str, fmt: str) -> str:
    return absolute_url(reverse("feeds:feed", kwargs={"key": key, "fmt": fmt}))


def _site() -> str:
    return get_site_settings().site_name or "Mobofirm"


# -------------------------------------------------------------------- items ----


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return EPOCH
    return moment if timezone.is_aware(moment) else timezone.make_aware(moment)


def _byline(post: Any) -> str:
    author = getattr(post, "author", None)
    if getattr(post, "content_source", "") == "automated" or author is None:
        return str(getattr(settings, "BLOG_EDITORIAL_NAME", "Mobofirm Editorial"))
    return str(getattr(author, "public_display_name", "") or _site())


def _image(post: Any) -> str:
    return absolute_url(str(getattr(post, "cover_image_url", "") or ""))


def _post_item(post: Any) -> FeedItem | None:
    url = absolute_url(post.get_absolute_url())
    if not url:
        return None
    gated = bool(getattr(post, "is_gated", False))
    summary = content_module.teaser(post.summary or "", 300)
    html = f"<p>{escape(summary)}</p>" if gated else str(post.body or "")
    if gated and url:
        html += f'<p><a href="{escape(url)}">Read the full article</a></p>'
    published = _aware(post.published_at or post.publish_at)
    tags = []
    for tag in post.tags.all():
        name = str(getattr(tag, "name", ""))
        if name:
            tags.append(name)
    return FeedItem(
        ref=f"post:{post.pk}",
        title=post.title,
        url=url,
        summary=summary,
        html=html,
        published=published,
        updated=max(published, _aware(getattr(post, "updated_at", None))),
        author=_byline(post),
        categories=tuple(tags[:8]),
        image_url=_image(post),
        kind="blog",
    )


def blog_items(
    limit: int,
    *,
    tag: str = "",
    brand: str = "",
    chipset: str = "",
    since: datetime | None = None,
) -> list[FeedItem]:
    if not (apps.is_installed("apps.blog") and app_enabled("blog")):
        return []
    post_model = apps.get_model("blog", "Post")
    posts = (
        post_model.published()
        .select_related("author")
        .prefetch_related("tags")
        .order_by("-published_at", "-pk")
    )
    if tag:
        posts = posts.filter(tags__slug=tag)
    if brand:
        posts = posts.filter(firmware_brand__slug=brand)
    if chipset:
        posts = posts.filter(firmware_model__chipset_ref__slug=chipset)
    if since is not None:
        posts = posts.filter(published_at__gte=since)
    found = (_post_item(post) for post in posts.distinct()[:limit])
    return [item for item in found if item is not None]


def _firmware_item(firmware: Any, firmware_type: str) -> FeedItem | None:
    item = content_module.firmware_content(firmware, firmware_type, str(firmware.pk))
    if item is None:
        return None
    details = [
        part
        for part in (
            f"Build {item.build_number}" if item.build_number else "",
            f"Android {item.android_version}" if item.android_version else "",
            item.chipset,
        )
        if part
    ]
    summary = f"New verified firmware for {item.title}. " + " · ".join(details)
    html = f"<p>{escape(summary.strip())}</p>"
    html += f'<p><a href="{escape(item.url)}">Open the firmware page</a></p>'
    published = _aware(
        getattr(firmware, "reviewed_at", None) or getattr(firmware, "created_at", None)
    )
    return FeedItem(
        ref=item.object_ref,
        title=f"{item.title} firmware",
        url=item.url,
        summary=summary.strip(),
        html=html,
        published=published,
        updated=max(published, _aware(getattr(firmware, "updated_at", None))),
        author=_site(),
        categories=tuple(item.tags),
        image_url=item.image_url,
        kind="firmware",
    )


def firmware_items(limit: int, *, brand: str = "", chipset: str = "") -> list[FeedItem]:
    if not (apps.is_installed("apps.firmwares") and app_enabled("firmwares")):
        return []
    found: list[FeedItem] = []
    for firmware_type in FIRMWARE_TYPES:
        try:
            model = apps.get_model("firmwares", f"{firmware_type}firmware")
        except LookupError:
            continue
        rows = (
            model.objects.select_related(
                "brand",
                "model",
                "variant",
                "model__chipset_ref__vendor",
                "variant__chipset_ref__vendor",
            )
            .filter(is_verified=True, is_active=True)
            .order_by("-created_at")
        )
        if brand:
            rows = rows.filter(brand__slug=brand)
        if chipset:
            rows = rows.filter(
                Q(model__chipset_ref__slug=chipset)
                | Q(variant__chipset_ref__slug=chipset)
            )
        for row in rows[:limit]:
            item = _firmware_item(row, firmware_type)
            if item is not None:
                found.append(item)
    found.sort(key=lambda item: item.published, reverse=True)
    return found[:limit]


def _news_locs() -> set[str] | None:
    """URLs the Google News sitemap lists (``None`` when it is not enabled)."""
    if not apps.is_installed("apps.seo"):
        return None
    seo_settings = apps.get_model("seo", "SEOSettings").get_solo()
    if not (seo_settings.sitemap_news_enabled and seo_settings.seo_enabled):
        return None
    entries = sitemap_build.news_entries(seo_settings, absolute_url("/").rstrip("/"))
    return {entry.loc for entry in entries}


def items_for(spec: Spec, limit: int) -> list[FeedItem]:
    if spec.scope == "blog":
        return blog_items(limit)
    if spec.scope == "firmware":
        return firmware_items(limit)
    if spec.scope == "tag":
        return blog_items(limit, tag=spec.slug)
    if spec.scope in {"brand", "chipset"}:
        if spec.scope == "brand":
            merged = blog_items(limit, brand=spec.slug) + firmware_items(
                limit, brand=spec.slug
            )
        else:
            merged = blog_items(limit, chipset=spec.slug) + firmware_items(
                limit, chipset=spec.slug
            )
        merged.sort(key=lambda item: item.published, reverse=True)
        return merged[:limit]
    if spec.scope == "news":
        locs = _news_locs()
        if locs is None:
            return []
        since = timezone.now() - NEWS_WINDOW
        return [item for item in blog_items(limit, since=since) if item.url in locs]
    merged = blog_items(limit) + firmware_items(limit)
    merged.sort(key=lambda item: item.published, reverse=True)
    return merged[:limit]


def news_available() -> bool:
    return _news_locs() is not None


# ------------------------------------------------------------------ writers ----


def _rfc822(moment: datetime) -> str:
    return format_datetime(moment.astimezone(UTC), usegmt=True)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _el(parent: ET.Element, tag: str, text: str = "", **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, tag, attrs)
    if text:
        child.text = text
    return child


def _register_namespaces() -> None:
    for prefix, uri in _NS.items():
        ET.register_namespace(prefix, uri)


def _q(prefix: str, name: str) -> str:
    return f"{{{_NS[prefix]}}}{name}"


def _image_type(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for suffix, mime in (
        (".png", "image/png"),
        (".webp", "image/webp"),
        (".gif", "image/gif"),
    ):
        if path.endswith(suffix):
            return mime
    return "image/jpeg"


def _tostring(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _modified(items: list[FeedItem]) -> datetime:
    return max((item.updated for item in items), default=EPOCH)


def _language() -> str:
    return (get_site_settings().default_language or "en").split("-")[0].lower()


def render_rss(spec: Spec, items: list[FeedItem], hub: str) -> bytes:
    _register_namespaces()
    self_url = feed_url(spec.key, "rss")
    root = ET.Element("rss", {"version": "2.0"})
    channel = _el(root, "channel")
    _el(channel, "title", spec.title)
    _el(channel, "link", absolute_url(reverse("feeds:index")))
    _el(channel, "description", spec.description)
    _el(channel, "language", _language())
    _el(channel, "lastBuildDate", _rfc822(_modified(items)))
    _el(channel, "ttl", str(TTL_MINUTES))
    _el(channel, "generator", "Mobofirm distribution")
    _el(channel, _q("atom", "link"), href=self_url, rel="self", type=FORMATS["rss"][0])
    if hub:
        _el(channel, _q("atom", "link"), href=hub, rel="hub")
    for item in items:
        node = _el(channel, "item")
        _el(node, "title", item.title)
        _el(node, "link", item.url)
        _el(node, "guid", item.url, isPermaLink="true")
        _el(node, "pubDate", _rfc822(item.published))
        _el(node, _q("dc", "creator"), item.author)
        _el(node, "description", item.summary)
        _el(node, _q("content", "encoded"), item.html)
        for category in item.categories:
            _el(node, "category", category)
        if item.image_url:
            _el(
                node,
                _q("media", "content"),
                url=item.image_url,
                medium="image",
                type=_image_type(item.image_url),
            )
    return _tostring(root)


def render_atom(spec: Spec, items: list[FeedItem], hub: str) -> bytes:
    _register_namespaces()
    self_url = feed_url(spec.key, "atom")
    root = ET.Element(_q("atom", "feed"))
    root.set("{http://www.w3.org/XML/1998/namespace}lang", _language())
    _el(root, _q("atom", "title"), spec.title)
    _el(root, _q("atom", "id"), self_url)
    _el(root, _q("atom", "updated"), _iso(_modified(items)))
    _el(root, _q("atom", "subtitle"), spec.description)
    _el(root, _q("atom", "link"), href=self_url, rel="self", type=FORMATS["atom"][0])
    _el(
        root,
        _q("atom", "link"),
        href=absolute_url(reverse("feeds:index")),
        rel="alternate",
    )
    if hub:
        _el(root, _q("atom", "link"), href=hub, rel="hub")
    author = _el(root, _q("atom", "author"))
    _el(author, _q("atom", "name"), _site())
    for item in items:
        entry = _el(root, _q("atom", "entry"))
        _el(entry, _q("atom", "title"), item.title)
        _el(entry, _q("atom", "id"), item.url)
        _el(entry, _q("atom", "link"), href=item.url, rel="alternate", type="text/html")
        _el(entry, _q("atom", "published"), _iso(item.published))
        _el(entry, _q("atom", "updated"), _iso(item.updated))
        writer = _el(entry, _q("atom", "author"))
        _el(writer, _q("atom", "name"), item.author)
        _el(entry, _q("atom", "summary"), item.summary)
        _el(entry, _q("atom", "content"), item.html, type="html")
        for category in item.categories:
            _el(entry, _q("atom", "category"), term=category)
        if item.image_url:
            _el(
                entry,
                _q("media", "content"),
                url=item.image_url,
                medium="image",
                type=_image_type(item.image_url),
            )
    return _tostring(root)


def render_json(spec: Spec, items: list[FeedItem], hub: str) -> bytes:
    payload: dict[str, Any] = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": spec.title,
        "home_page_url": absolute_url(reverse("feeds:index")),
        "feed_url": feed_url(spec.key, "json"),
        "description": spec.description,
        "language": _language(),
        "items": [
            {
                "id": item.url,
                "url": item.url,
                "title": item.title,
                "summary": item.summary,
                "content_html": item.html,
                "date_published": _iso(item.published),
                "date_modified": _iso(item.updated),
                "authors": [{"name": item.author}],
                "tags": list(item.categories),
                **({"image": item.image_url} if item.image_url else {}),
            }
            for item in items
        ],
    }
    if hub:
        payload["hubs"] = [{"type": "WebSub", "url": hub}]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


RENDERERS = {"rss": render_rss, "atom": render_atom, "json": render_json}


def effective_limit(cfg: DistributionSettings) -> int:
    return max(1, min(int(cfg.feed_item_limit or 30), 100))


def build(spec: Spec, fmt: str, cfg: DistributionSettings | None = None) -> Built:
    """Render ``spec`` as ``fmt``; the result depends only on the public content."""
    cfg = cfg or DistributionSettings.get_solo()
    items = items_for(spec, effective_limit(cfg))
    body = RENDERERS[fmt](spec, items, cfg.websub_hub_url or "")
    etag = 'W/"' + hashlib.sha1(body, usedforsecurity=False).hexdigest()[:24] + '"'
    return Built(spec, fmt, body, FORMATS[fmt][0], etag, _modified(items), len(items))


# --------------------------------------------------------------- validation ----


def validate(body: bytes, fmt: str) -> list[str]:
    """Problems that would make a reader reject the feed (empty list = valid)."""
    problems: list[str] = []
    if fmt == "json":
        try:
            data = json.loads(body)
        except ValueError as error:
            return [f"Not valid JSON: {error}"]
        for field in ("version", "title", "items"):
            if field not in data:
                problems.append(f"JSON Feed is missing '{field}'.")
        for index, item in enumerate(data.get("items", []), 1):
            if not item.get("id"):
                problems.append(f"Item {index} has no id.")
            if not (item.get("content_html") or item.get("content_text")):
                problems.append(f"Item {index} has no content.")
        return problems
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        return [f"Not well-formed XML: {error}"]
    if fmt == "rss":
        channel = root.find("channel")
        if root.tag != "rss" or channel is None:
            return ["Not an RSS 2.0 document."]
        for tag in ("title", "link", "description", "lastBuildDate"):
            if not (channel.findtext(tag) or "").strip():
                problems.append(f"Channel is missing <{tag}>.")
        if channel.find(_q("atom", "link")) is None:
            problems.append("Channel has no atom:link rel=self.")
        for index, item in enumerate(channel.findall("item"), 1):
            for tag in ("title", "link", "guid", "pubDate"):
                if not (item.findtext(tag) or "").strip():
                    problems.append(f"Item {index} is missing <{tag}>.")
        return problems
    if root.tag != _q("atom", "feed"):
        return ["Not an Atom 1.0 document."]
    for tag in ("id", "title", "updated"):
        if not (root.findtext(_q("atom", tag)) or "").strip():
            problems.append(f"Feed is missing <{tag}>.")
    for index, entry in enumerate(root.findall(_q("atom", "entry")), 1):
        for tag in ("id", "title", "updated"):
            if not (entry.findtext(_q("atom", tag)) or "").strip():
                problems.append(f"Entry {index} is missing <{tag}>.")
        if entry.find(_q("atom", "link")) is None:
            problems.append(f"Entry {index} has no link.")
    return problems


# ------------------------------------------------------ conditional requests ----


def not_modified(
    etag: str, modified: datetime, if_none_match: str, if_modified_since: str
) -> bool:
    """RFC 9110 order: ``If-None-Match`` decides; ``If-Modified-Since`` only without it."""
    if if_none_match:
        wanted = {part.strip().removeprefix("W/") for part in if_none_match.split(",")}
        return "*" in wanted or etag.removeprefix("W/") in wanted
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since)
        except (TypeError, ValueError):
            return False
        return modified.replace(microsecond=0) <= _aware(since)
    return False


def slug_of(value: str) -> str:
    return slugify(value)
