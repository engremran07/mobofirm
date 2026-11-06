"""Technical audit: real checks over crawler observations and the page inventory.

There is no synthetic "SEO score". ``CrawlRun.health_percent`` is the share of
crawled pages that have no error-level issue; every issue records the evidence it
was measured from.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from apps.site_settings.services import get_site_settings

from .inventory import build_inventory, resolve_path
from .models import CrawlRun, Issue, PageObservation
from .siteurls import is_absolute_http

ERROR, WARNING, INFO = Issue.Severity.ERROR, Issue.Severity.WARNING, Issue.Severity.INFO
HREFLANG_CODE = re.compile(r"^(x-default|[a-z]{2,3}(-[A-Za-z0-9]{2,8})*)$")
SLOW_MS = 3000
REFERRER_LIMIT = 5


@dataclass
class Finding:
    code: str
    severity: str
    url: str
    message: str
    key: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        raw = f"{self.code}|{self.url}|{self.key}"
        return hashlib.sha256(raw.encode()).hexdigest()


def _noindex(observation: PageObservation) -> bool:
    directives = " ".join([*observation.robots_meta, observation.x_robots_tag]).lower()
    return "noindex" in directives


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def analyze_observations(observations: list[PageObservation]) -> list[Finding]:
    findings: list[Finding] = []
    by_url = {obs.url: obs for obs in observations}
    inbound: dict[str, list[str]] = defaultdict(list)
    for obs in observations:
        for link in obs.internal_links:
            inbound[link].append(obs.url)
    homepage = min(
        (obs.url for obs in observations if obs.depth == 0), default="", key=len
    )

    def add(code: str, severity: str, obs_url: str, message: str, **kw: Any) -> None:
        key = str(kw.pop("key", ""))
        findings.append(Finding(code, severity, obs_url, message, key, kw))

    titles: dict[str, list[str]] = defaultdict(list)
    descriptions: dict[str, list[str]] = defaultdict(list)

    for obs in observations:
        if obs.blocked_by_robots:
            if obs.in_sitemap:
                add(
                    "robots_blocked_in_sitemap",
                    ERROR,
                    obs.url,
                    "A sitemap URL is blocked by robots.txt.",
                )
            continue
        if obs.fetch_error:
            add("fetch_failed", ERROR, obs.url, f"Fetch failed: {obs.fetch_error}")
            continue
        chain = obs.redirect_chain
        if chain:
            if len(chain) > 1:
                add(
                    "redirect_chain",
                    WARNING,
                    obs.url,
                    f"{len(chain)} redirects before the final URL.",
                    source=_path(obs.url),
                    chain=[hop["to"] for hop in chain],
                    final=_path(chain[-1]["to"]),
                )
            if obs.in_sitemap:
                add(
                    "sitemap_url_redirects",
                    WARNING,
                    obs.url,
                    "A sitemap URL redirects.",
                )
        status = obs.status_code or 0
        if status >= 400:
            if obs.in_sitemap:
                add(
                    "sitemap_url_error",
                    ERROR,
                    obs.url,
                    f"A sitemap URL returns HTTP {status}.",
                )
            continue
        if obs.response_ms > SLOW_MS:
            add("slow_response", INFO, obs.url, f"Response took {obs.response_ms} ms.")
        if (
            not obs.content_type.startswith(("text/html", "application/xhtml"))
            or status != 200
        ):
            continue

        noindex = _noindex(obs)
        if len(obs.titles) == 0:
            add("missing_title", ERROR, obs.url, "The page has no <title>.")
        elif len(obs.titles) > 1:
            add(
                "multiple_titles", ERROR, obs.url, "The page has more than one <title>."
            )
        elif obs.titles[0] and not noindex:
            titles[obs.titles[0]].append(obs.url)
        if len(obs.descriptions) == 0:
            if not noindex:
                add(
                    "missing_description",
                    WARNING,
                    obs.url,
                    "The page has no meta description.",
                )
        elif len(obs.descriptions) > 1:
            add(
                "multiple_descriptions",
                ERROR,
                obs.url,
                "More than one meta description.",
            )
        elif obs.descriptions[0] and not noindex:
            descriptions[obs.descriptions[0]].append(obs.url)
        if len(obs.robots_meta) > 1:
            add(
                "multiple_robots_meta", ERROR, obs.url, "More than one robots meta tag."
            )
        if len(obs.canonicals) > 1:
            add("multiple_canonicals", ERROR, obs.url, "More than one canonical link.")
        elif not obs.canonicals and not noindex:
            add(
                "missing_canonical",
                WARNING,
                obs.url,
                "An indexable page has no canonical link.",
            )
        for canonical in obs.canonicals[:1]:
            if not is_absolute_http(canonical):
                add(
                    "canonical_not_absolute",
                    ERROR,
                    obs.url,
                    "The canonical URL is not absolute.",
                    key=canonical,
                )
                continue
            target = by_url.get(canonical)
            if (
                target is not None
                and (target.status_code or 0) != 200
                and not target.redirect_chain
            ):
                add(
                    "canonical_to_error",
                    ERROR,
                    obs.url,
                    f"The canonical URL returns HTTP {target.status_code}.",
                    key=canonical,
                )
            if obs.in_sitemap and canonical != obs.url:
                add(
                    "sitemap_canonical_mismatch",
                    ERROR,
                    obs.url,
                    "A sitemap URL declares a different canonical.",
                    key=canonical,
                    canonical=canonical,
                )
        if obs.in_sitemap and noindex:
            add(
                "noindex_in_sitemap",
                ERROR,
                obs.url,
                "A noindex page is listed in the sitemap.",
            )
        if not obs.h1s and not noindex:
            add("missing_h1", WARNING, obs.url, "The page has no <h1>.")
        elif len(obs.h1s) > 1:
            add(
                "multiple_h1",
                INFO,
                obs.url,
                f"The page has {len(obs.h1s)} <h1> elements.",
            )
        if obs.jsonld_errors:
            add(
                "invalid_json_ld",
                ERROR,
                obs.url,
                f"{obs.jsonld_errors} JSON-LD block(s) are not valid.",
            )
        if obs.images_missing_alt:
            add(
                "images_missing_alt",
                INFO,
                obs.url,
                f"{obs.images_missing_alt} image(s) lack an alt attribute.",
            )
        if not noindex and obs.url != homepage and not inbound.get(obs.url):
            add(
                "orphan_page",
                WARNING,
                obs.url,
                "No crawled page links to this indexable page.",
            )

        for link in obs.internal_links:
            target = by_url.get(link)
            if target is None:
                continue
            if target.fetch_error or (target.status_code or 0) >= 400:
                add(
                    "broken_internal_link",
                    ERROR,
                    obs.url,
                    f"Links to {_path(link)} which returns {target.status_code or 'an error'}.",
                    key=link,
                    target=link,
                )
            elif target.redirect_chain:
                add(
                    "redirect_internal_link",
                    WARNING,
                    obs.url,
                    f"Links to {_path(link)} which redirects.",
                    key=link,
                    target=link,
                    final=target.redirect_chain[-1]["to"],
                )

        findings.extend(_hreflang(obs, by_url))
        findings.extend(_head_mismatch(obs))

    for title, urls in titles.items():
        if len(urls) > 1:
            for url in urls:
                add(
                    "duplicate_title",
                    WARNING,
                    url,
                    f"Title shared by {len(urls)} pages.",
                    key=title,
                    title=title,
                    pages=urls[:REFERRER_LIMIT],
                )
    for text, urls in descriptions.items():
        if len(urls) > 1:
            for url in urls:
                add(
                    "duplicate_description",
                    WARNING,
                    url,
                    f"Description shared by {len(urls)} pages.",
                    key=text,
                    pages=urls[:REFERRER_LIMIT],
                )
    return findings


def _hreflang(
    obs: PageObservation, by_url: dict[str, PageObservation]
) -> list[Finding]:
    found: list[Finding] = []
    if not obs.hreflang:
        return found
    for entry in obs.hreflang:
        code, href = entry.get("lang", ""), entry.get("href", "")
        if not HREFLANG_CODE.match(code):
            found.append(
                Finding(
                    "hreflang_invalid_code",
                    ERROR,
                    obs.url,
                    f"Invalid hreflang code {code!r}.",
                    key=code,
                )
            )
        other = by_url.get(href)
        returns = other is not None and any(
            e.get("href") == obs.url for e in other.hreflang
        )
        if other is not None and href != obs.url and not returns:
            found.append(
                Finding(
                    "hreflang_missing_return",
                    ERROR,
                    obs.url,
                    f"{href} does not link back.",
                    key=href,
                )
            )
    if not any(e.get("href") in {obs.url, *obs.canonicals} for e in obs.hreflang):
        found.append(
            Finding(
                "hreflang_missing_self",
                WARNING,
                obs.url,
                "hreflang set lacks a self reference.",
            )
        )
    return found


def _head_mismatch(obs: PageObservation) -> list[Finding]:
    """Compare the raw HTML head with what the resolver intended for the path.

    After a redirect the head belongs to the final URL, so that is what is compared.
    """
    expected = resolve_path(_path(obs.final_url or obs.url))
    if expected is None or len(obs.titles) != 1:
        return []
    actual_noindex = _noindex(obs)
    mismatches: dict[str, dict[str, Any]] = {}
    if obs.titles[0] != expected.title:
        mismatches["title"] = {"expected": expected.title, "actual": obs.titles[0]}
    if expected.canonical and obs.canonicals[:1] != [expected.canonical]:
        mismatches["canonical"] = {
            "expected": expected.canonical,
            "actual": obs.canonicals[:1],
        }
    if actual_noindex == expected.indexable:
        mismatches["robots"] = {
            "expected_indexable": expected.indexable,
            "actual_noindex": actual_noindex,
        }
    if not mismatches:
        return []
    return [
        Finding(
            "head_mismatch",
            ERROR,
            obs.url,
            "The served head differs from the resolver output: "
            + ", ".join(sorted(mismatches)),
            details={"fields": mismatches},
        )
    ]


def analyze_inventory() -> list[Finding]:
    """Checks that need no HTTP: sitemap membership versus indexation policy."""
    findings = [
        Finding(
            "inventory_conflict",
            ERROR,
            item.path,
            f"Sitemap URL conflicts with policy: {conflict}.",
            key=conflict,
            details={"page_type": item.page_type, "reason": item.reason},
        )
        for item in build_inventory()
        for conflict in item.conflicts
    ]
    if not get_site_settings().site_url:
        findings.append(
            Finding(
                "site_url_missing",
                WARNING,
                "",
                "SITE_URL is not configured; canonical URLs fall back to the request host.",
            )
        )
    return findings


INVENTORY_CODES = frozenset({"inventory_conflict", "site_url_missing"})


def sync_issues(
    run: CrawlRun | None,
    findings: list[Finding],
    observed: set[str],
    *,
    inventory: bool = False,
) -> int:
    """Upsert findings by fingerprint and resolve issues that no longer reproduce.

    Only issues from the same analyzer (inventory versus crawl) are resolved.
    """
    seen: set[str] = set()
    for finding in findings:
        seen.add(finding.fingerprint)
        issue, created = Issue.objects.get_or_create(
            fingerprint=finding.fingerprint,
            defaults={
                "code": finding.code,
                "severity": finding.severity,
                "url": finding.url,
                "message": finding.message[:500],
                "details": finding.details,
                "first_seen_run": run,
                "last_seen_run": run,
            },
        )
        if not created:
            reopened = issue.status == Issue.Status.RESOLVED
            issue.message = finding.message[:500]
            issue.severity = finding.severity
            issue.details = finding.details
            issue.last_seen_run = run
            if reopened:
                issue.status = Issue.Status.OPEN
            issue.save()
    stale = Issue.objects.filter(status=Issue.Status.OPEN).exclude(fingerprint__in=seen)
    stale = (
        stale.filter(code__in=INVENTORY_CODES)
        if inventory
        else stale.exclude(code__in=INVENTORY_CODES)
    )
    resolved = 0
    for issue in stale:
        if (
            inventory
            or issue.url == ""
            or issue.url in observed
            or _path(issue.url) in observed
        ):
            issue.status = Issue.Status.RESOLVED
            issue.save()
            resolved += 1
    return resolved


def run_audit(run: CrawlRun) -> CrawlRun:
    observations = list(run.observations.all())
    crawl_findings = analyze_observations(observations)
    observed = {obs.url for obs in observations}
    observed |= {_path(url) for url in observed}
    sync_issues(run, crawl_findings, observed)
    inventory_findings = analyze_inventory()
    sync_issues(run, inventory_findings, set(), inventory=True)
    error_urls = {
        finding.url
        for finding in crawl_findings
        if finding.severity == ERROR and finding.url in {o.url for o in observations}
    }
    run.pages_with_errors = len(error_urls)
    run.status = CrawlRun.Status.COMPLETED
    run.save()
    return run
