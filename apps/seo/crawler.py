"""Internal crawler: bounded, robots-aware, fetching only through ``SafeFetcher``.

It records what the server actually sent (raw HTTP HTML), never what the resolver
intended; ``audit`` then compares the two.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from django.db import DatabaseError
from django.utils import timezone

from . import audit, autonomy
from .fetcher import FetchError, FetchResult, SafeFetcher, default_fetcher
from .models import CrawlRun, PageObservation, SEOSettings
from .siteurls import site_origin

USER_AGENT_TOKEN = "MobofirmSEOBot"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
MAX_SITEMAP_FILES = 60
ASSET_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".zip",
    ".rar",
    ".7z",
    ".pdf",
    ".bin",
    ".tar",
    ".gz",
    ".mp4",
    ".woff",
    ".woff2",
)
_NON_HTTP = re.compile(r"^(mailto|tel|javascript|data|sms):", re.IGNORECASE)


@dataclass
class ParsedPage:
    titles: list[str] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    robots_meta: list[str] = field(default_factory=list)
    canonicals: list[str] = field(default_factory=list)
    hreflang: list[dict[str, str]] = field(default_factory=list)
    h1s: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    jsonld_types: list[str] = field(default_factory=list)
    jsonld_errors: int = 0
    images_missing_alt: int = 0
    lang: str = ""


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._text_target: str | None = None
        self._buffer: list[str] = []
        self._in_jsonld = False
        self._jsonld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "html":
            self.page.lang = values.get("lang", "")
        elif tag in {"title", "h1"}:
            self._text_target, self._buffer = tag, []
        elif tag == "meta":
            name = values.get("name", "").lower()
            if name == "description":
                self.page.descriptions.append(values.get("content", ""))
            elif name == "robots":
                self.page.robots_meta.append(values.get("content", ""))
        elif tag == "link":
            relations = values.get("rel", "").lower().split()
            if "canonical" in relations:
                self.page.canonicals.append(values.get("href", ""))
            elif "alternate" in relations and values.get("hreflang"):
                self.page.hreflang.append(
                    {"lang": values["hreflang"], "href": values.get("href", "")}
                )
        elif tag == "a" and values.get("href"):
            self.page.links.append(values["href"])
        elif tag == "img" and "alt" not in values:
            self.page.images_missing_alt += 1
        elif (
            tag == "script" and values.get("type", "").lower() == "application/ld+json"
        ):
            self._in_jsonld, self._jsonld = True, []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld.append(data)
        elif self._text_target:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._text_target:
            text = " ".join("".join(self._buffer).split())
            (self.page.titles if tag == "title" else self.page.h1s).append(text)
            self._text_target = None
        elif tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self._record_jsonld("".join(self._jsonld))

    def _record_jsonld(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.page.jsonld_errors += 1
            return
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type"):
                kind = node["@type"]
                self.page.jsonld_types.append(
                    kind if isinstance(kind, str) else ",".join(map(str, kind))
                )
            else:
                self.page.jsonld_errors += 1


def parse_html(markup: str) -> ParsedPage:
    parser = _HeadParser()
    parser.feed(markup)
    parser.close()
    return parser.page


def normalize(href: str, base: str, origin: str) -> str | None:
    """Same-site absolute URL without fragment, or ``None`` for anything else."""
    if not href or _NON_HTTP.match(href.strip()):
        return None
    absolute, _fragment = urldefrag(urljoin(base, href.strip()))
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"}:
        return None
    if f"{parts.scheme}://{parts.netloc}".lower() != origin.lower():
        return None
    if parts.path.lower().endswith(ASSET_SUFFIXES):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """(child sitemap URLs, page URLs) from a sitemap or sitemap index document."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return [], []
    children = [
        (node.findtext(f"{SITEMAP_NS}loc") or "").strip()
        for node in root.findall(f"{SITEMAP_NS}sitemap")
    ]
    pages = [
        (node.findtext(f"{SITEMAP_NS}loc") or "").strip()
        for node in root.findall(f"{SITEMAP_NS}url")
    ]
    return [c for c in children if c], [p for p in pages if p]


class CrawlUnavailable(Exception):
    """A crawl cannot start (disabled, unconfigured or already running)."""


def run_crawl(
    *,
    trigger: str = CrawlRun.Trigger.MANUAL,
    user: object | None = None,
    base_url: str = "",
    fetcher: SafeFetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlRun:
    """Crawl the site, audit the observations and plan safe follow-up changes."""
    row = SEOSettings.get_solo()
    if not row.seo_enabled or row.autonomy_mode == SEOSettings.AutonomyMode.OFF:
        raise CrawlUnavailable("SEO automation is switched off.")
    origin = base_url or site_origin()
    if not origin:
        raise CrawlUnavailable("Configure SITE_URL before crawling.")
    cutoff = timezone.now() - timedelta(hours=2)
    if CrawlRun.objects.filter(
        status=CrawlRun.Status.RUNNING, created_at__gte=cutoff
    ).exists():
        raise CrawlUnavailable("A crawl is already running.")
    run = CrawlRun.objects.create(
        base_url=origin,
        trigger=trigger,
        max_pages=row.crawl_max_pages,
        initiated_by_id=(
            getattr(user, "pk", None)
            if getattr(user, "is_authenticated", False)
            else None
        ),
    )
    completed = False
    try:
        Crawler(
            origin,
            max_pages=row.crawl_max_pages,
            delay_ms=row.crawl_delay_ms,
            fetcher=fetcher or default_fetcher(),
            sleep=sleep,
        ).run(run)
        audit.run_audit(run)
        autonomy.plan_from_issues()
        completed = True
    except (FetchError, DatabaseError, OSError, ValueError) as error:
        run.error = str(error)[:2000]
        raise
    finally:
        run.finished_at = timezone.now()
        if not completed:
            run.status = CrawlRun.Status.FAILED
            run.error = run.error or "Crawl aborted unexpectedly."
        run.save()
    return run


@dataclass
class Crawler:
    base_url: str
    max_pages: int = 300
    delay_ms: int = 0
    fetcher: SafeFetcher = field(default_factory=default_fetcher)
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.hosts = frozenset({(parts.hostname or "").lower()})
        self.robots = RobotFileParser()
        self.robots_status: int | None = None
        self.sitemap_urls: set[str] = set()

    def _get(self, url: str) -> FetchResult:
        return self.fetcher.fetch(url, allowed_hosts=self.hosts)

    def _load_robots(self) -> None:
        try:
            result = self._get(f"{self.origin}/robots.txt")
        except FetchError:
            self.robots.parse([])
            return
        self.robots_status = result.status
        self.robots.parse(result.text.splitlines() if result.status == 200 else [])

    def _load_sitemaps(self) -> None:
        pending = deque([f"{self.origin}/sitemap.xml"])
        seen: set[str] = set()
        while pending and len(seen) < MAX_SITEMAP_FILES:
            location = pending.popleft()
            if location in seen:
                continue
            seen.add(location)
            normalized = normalize(location, self.origin, self.origin)
            if normalized is None:
                continue
            try:
                result = self._get(normalized)
            except FetchError:
                continue
            if result.status != 200:
                continue
            children, pages = parse_sitemap(result.text)
            pending.extend(children)
            for page in pages:
                url = normalize(page, self.origin, self.origin)
                if url:
                    self.sitemap_urls.add(url)

    def run(self, run: CrawlRun) -> CrawlRun:
        run.status = CrawlRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save()
        self._load_robots()
        run.robots_status = self.robots_status
        self._load_sitemaps()

        start = normalize("/", self.origin, self.origin) or f"{self.origin}/"
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        queued = {start}
        for url in sorted(self.sitemap_urls - queued):
            queue.append((url, 1))
            queued.add(url)
        crawled = 0
        while queue and crawled < min(self.max_pages, run.max_pages):
            url, depth = queue.popleft()
            observation = self._observe(run, url, depth)
            crawled += 1
            for target in [*observation.internal_links, observation.final_url]:
                if target and target not in queued and not self._skip(target):
                    queued.add(target)
                    queue.append((target, depth + 1))
            if self.delay_ms:
                self.sleep(self.delay_ms / 1000)
        run.pages_crawled = crawled
        run.save()
        return run

    def _skip(self, url: str) -> bool:
        return not url.startswith(self.origin)

    def _observe(self, run: CrawlRun, url: str, depth: int) -> PageObservation:
        observation = PageObservation(
            run=run, url=url, depth=depth, in_sitemap=url in self.sitemap_urls
        )
        if not self.robots.can_fetch(USER_AGENT_TOKEN, url):
            observation.blocked_by_robots = True
            observation.save()
            return observation
        try:
            result = self._get(url)
        except FetchError as error:
            observation.fetch_error = f"{error.code}: {error}"[:500]
            observation.save()
            return observation
        observation.status_code = result.status
        observation.final_url = result.final_url
        observation.redirect_chain = [
            {"status": status, "from": source, "to": target}
            for status, source, target in result.redirects
        ]
        observation.content_type = result.content_type[:100]
        observation.response_ms = result.elapsed_ms
        observation.size_bytes = len(result.body)
        observation.x_robots_tag = result.headers.get("x-robots-tag", "")[:255]
        if result.status == 200 and result.content_type in {
            "text/html",
            "application/xhtml+xml",
        }:
            parsed = parse_html(result.text)
            links = dict.fromkeys(
                link
                for href in parsed.links
                if (link := normalize(href, result.final_url, self.origin))
            )
            observation.titles = parsed.titles
            observation.descriptions = parsed.descriptions
            observation.canonicals = parsed.canonicals
            observation.robots_meta = parsed.robots_meta
            observation.h1s = parsed.h1s
            observation.lang = parsed.lang[:20]
            observation.hreflang = parsed.hreflang
            observation.jsonld_types = parsed.jsonld_types
            observation.jsonld_errors = parsed.jsonld_errors
            observation.images_missing_alt = parsed.images_missing_alt
            observation.internal_links = list(links)
        observation.save()
        return observation
