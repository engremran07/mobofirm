"""Data for the staff "Sitemaps & robots" screen: what is published, and is it valid.

Everything here reads the same engine the public endpoints use
(:mod:`apps.seo.sitemap_build`, :mod:`apps.seo.robots`); nothing is generated or stored
twice. Interactive costs are bounded: counts and limits come from one pass over the
listed entries, and the live resolver check samples a fixed number of URLs per section.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from .inventory import describe_entry
from .models import ChangeLedger, CrawlRun, PageObservation, SEOSettings
from .robots import ROBOTS_FIELDS, ParsedRobots, generate, parse
from .sitemap_build import (
    MAX_BYTES,
    MAX_URLS,
    SitemapFile,
    catalog,
    estimate_bytes,
    latest,
    news_entries,
)

SAMPLE_PER_SECTION = 15
HISTORY_LIMIT = 12
TEST_AGENTS = (
    "Googlebot",
    "Googlebot-Image",
    "bingbot",
    "GPTBot",
    "ClaudeBot",
    "OAI-SearchBot",
    "PerplexityBot",
    "*",
)


@dataclass
class SectionReport:
    section: str
    url_count: int = 0
    files: list[SitemapFile] = field(default_factory=list)
    lastmod: datetime | date | None = None
    size_bytes: int = 0
    problems: list[str] = field(default_factory=list)
    sampled: int = 0
    conflicts: list[str] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return len(self.files)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.conflicts


@dataclass
class SitemapReport:
    sections: list[SectionReport]
    duplicates: list[str]
    news_count: int
    crawl: CrawlRun | None
    crawl_errors: list[tuple[str, str]]
    validated_live: bool

    @property
    def total_urls(self) -> int:
        return sum(item.url_count for item in self.sections)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.sections) and not self.duplicates


def _sample(files: list[SitemapFile]) -> list[tuple[str, str]]:
    entries = [(entry.loc, file.section) for file in files for entry in file.entries]
    if len(entries) <= SAMPLE_PER_SECTION:
        return entries
    step = len(entries) / SAMPLE_PER_SECTION
    return [entries[int(index * step)] for index in range(SAMPLE_PER_SECTION)]


def build_report(
    row: SEOSettings, origin: str, *, validate_live: bool = False
) -> SitemapReport:
    by_section: dict[str, SectionReport] = {}
    seen: Counter[str] = Counter()
    for sitemap_file in catalog(row, origin):
        item = by_section.setdefault(
            sitemap_file.section, SectionReport(sitemap_file.section)
        )
        item.files.append(sitemap_file)
        item.url_count += len(sitemap_file.entries)
        item.size_bytes += estimate_bytes(len(sitemap_file.entries))
        seen.update(entry.loc for entry in sitemap_file.entries)
        if len(sitemap_file.entries) > MAX_URLS:
            item.problems.append(f"{sitemap_file.name} has more than {MAX_URLS} URLs.")
        if estimate_bytes(len(sitemap_file.entries)) > MAX_BYTES:
            item.problems.append(f"{sitemap_file.name} may exceed 50 MB.")
    for item in by_section.values():
        item.lastmod = latest(file.latest_lastmod for file in item.files)
        if validate_live:
            for loc, section in _sample(item.files):
                path = urlsplit(loc).path or "/"
                described = describe_entry(path, f"sitemap:{section}")
                item.sampled += 1
                if described is None:
                    item.conflicts.append(f"{path}: no route matches")
                elif described.conflicts:
                    item.conflicts.append(f"{path}: {', '.join(described.conflicts)}")
    crawl = CrawlRun.objects.filter(status=CrawlRun.Status.COMPLETED).first()
    crawl_errors: list[tuple[str, str]] = []
    if crawl is not None:
        failing = (
            PageObservation.objects.filter(run=crawl, in_sitemap=True)
            .exclude(status_code=200)
            .order_by("url")[:20]
        )
        crawl_errors = [
            (obs.url, obs.fetch_error or f"HTTP {obs.status_code}") for obs in failing
        ]
    return SitemapReport(
        sections=list(by_section.values()),
        duplicates=sorted(loc for loc, count in seen.items() if count > 1)[:20],
        news_count=len(news_entries(row, origin)),
        crawl=crawl,
        crawl_errors=crawl_errors,
        validated_live=validate_live,
    )


# --- robots ------------------------------------------------------------------------


@dataclass(frozen=True)
class UrlTest:
    url: str
    agent: str
    allowed: bool
    reason: str


def robots_history() -> list[ChangeLedger]:
    """Ledger entries that changed a setting which alters robots.txt, newest first."""
    entries = ChangeLedger.objects.filter(
        target_model="seo.seosettings", status=ChangeLedger.Status.APPLIED
    ).order_by("-created_at", "-pk")[:200]
    relevant = [
        entry
        for entry in entries
        if ROBOTS_FIELDS & {*entry.before.keys(), *entry.after.keys()}
    ]
    return relevant[:HISTORY_LIMIT]


def check_url(parsed: ParsedRobots, url: str, agent: str) -> UrlTest:
    decision = parsed.test(agent, url)
    return UrlTest(url, agent, decision.allowed, decision.reason)


def previous_robots(row: SEOSettings, origin: str) -> str | None:
    """The file as it was before the latest robots-relevant settings change."""
    history = robots_history()
    if not history:
        return None
    earlier = copy.copy(row)
    for name, value in history[0].before.items():
        if name in ROBOTS_FIELDS:
            setattr(earlier, name, value)
    return generate(earlier, origin)


def parse_live(row: SEOSettings, origin: str) -> ParsedRobots:
    return parse(generate(row, origin))
