"""robots.txt (RFC 9309): generation from the single settings authority, one parser.

``build_document`` turns :class:`SEOSettings` and :mod:`apps.seo.policy` into groups of
rules; :func:`render` writes the file; :func:`parse` reads any robots.txt (ours, or the
one the crawler fetched) with the RFC matching rules, so the staff URL tester, the
crawler and the sitemap image check all use the same decisions.

Matching (RFC 9309 section 2.2): a crawler obeys the one group whose ``User-agent``
token matches it best (``*`` only when none does); within the group the *longest*
matching pattern wins and ``Allow`` beats ``Disallow`` on a tie. ``*`` matches any run
of characters and a trailing ``$`` anchors the end of the URL. Noindex (not Disallow)
is used for thin or parameterised pages so crawlers can still read the directive;
Disallow is reserved for areas with no search value and for duplicate-URL traps.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.http import HttpRequest
from django.urls import reverse

from .contracts import POLICY_VERSION
from .models import SEOSettings
from .policy import (
    AI_SEARCH_BOTS,
    AI_TRAINING_BOTS,
    AI_USER_AGENTS,
    CRAWL_DELAY_BOTS,
    ROBOTS_ALLOW,
    ROBOTS_DISALLOW,
    ROBOTS_PARAM_TRAPS,
    SEARCH_BOTS,
)
from .siteurls import site_origin

MAX_BYTES = 500 * 1024  # RFC 9309 section 2.5: crawlers must parse at least 500 KiB.
# Vendor-documented fallbacks: these crawlers use the parent's group when they have
# none of their own.
PARENT_AGENT = {
    "googlebot-image": "googlebot",
    "googlebot-news": "googlebot",
    "googlebot-video": "googlebot",
}
# SEOSettings fields that change the generated file (used for the history view).
ROBOTS_FIELDS = frozenset(
    {
        "seo_enabled",
        "sitemap_enabled",
        "sitemap_news_enabled",
        "allow_ai_search_bots",
        "allow_ai_user_agents",
        "allow_ai_training_bots",
        "robots_crawl_delay",
    }
)


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str

    @property
    def line(self) -> str:
        return f"{'Allow' if self.allow else 'Disallow'}: {self.pattern}"


@dataclass(frozen=True)
class Group:
    agents: tuple[str, ...]
    rules: tuple[Rule, ...] = ()
    crawl_delay: int | None = None
    note: str = ""


@dataclass(frozen=True)
class Document:
    groups: tuple[Group, ...]
    sitemaps: tuple[str, ...] = ()
    origin: str = ""


@dataclass(frozen=True)
class Decision:
    """Outcome of testing one URL for one crawler."""

    allowed: bool
    agent: str  # the User-agent token of the group that applied ("" = no group)
    rule: Rule | None  # the winning rule; ``None`` means nothing matched

    @property
    def reason(self) -> str:
        if self.rule is None:
            where = f"group '{self.agent}'" if self.agent else "no group"
            return f"No rule in {where} matches, so crawling is allowed."
        return f"Group '{self.agent}': {self.rule.line}"


def sitemap_enabled(row: SEOSettings) -> bool:
    return row.seo_enabled and row.sitemap_enabled


# --- generation -----------------------------------------------------------------


def _param_rules() -> list[Rule]:
    rules: list[Rule] = []
    for name in ROBOTS_PARAM_TRAPS:
        rules.append(Rule(False, f"/*?{name}="))
        rules.append(Rule(False, f"/*&{name}="))
    return rules


def common_rules() -> tuple[Rule, ...]:
    """Private areas, parameter traps and the Allow exceptions, most specific first."""
    rules = [
        *(Rule(True, path) for path in ROBOTS_ALLOW),
        *(Rule(False, path) for path in ROBOTS_DISALLOW),
        *_param_rules(),
    ]
    rules.sort(key=lambda rule: (-len(rule.pattern), not rule.allow, rule.pattern))
    return tuple(rules)


def build_document(row: SEOSettings, origin: str) -> Document:
    """Groups and Sitemap lines implied by the settings row."""
    rules = common_rules()
    delay = row.robots_crawl_delay or None
    delayed = tuple(bot for bot in SEARCH_BOTS if delay and bot in CRAWL_DELAY_BOTS)
    shared = tuple(bot for bot in SEARCH_BOTS if bot not in delayed)
    groups = [
        Group(
            ("*", *shared),
            rules,
            note="Everyone, with explicit groups for Google and Bing crawlers",
        )
    ]
    groups.extend(
        Group(
            (bot,),
            rules,
            crawl_delay=delay,
            note=f"{bot} honours Crawl-delay; Google ignores it",
        )
        for bot in delayed
    )
    blocked = (
        (
            "AI search crawlers (disabled in SEO settings)",
            AI_SEARCH_BOTS,
            row.allow_ai_search_bots,
        ),
        (
            "AI user-triggered fetchers (disabled in SEO settings)",
            AI_USER_AGENTS,
            row.allow_ai_user_agents,
        ),
        (
            "AI training crawlers (disabled in SEO settings)",
            AI_TRAINING_BOTS,
            row.allow_ai_training_bots,
        ),
    )
    groups.extend(
        Group(agents, (Rule(False, "/"),), note=note)
        for note, agents, allowed in blocked
        if not allowed
    )
    sitemaps: list[str] = []
    if origin and sitemap_enabled(row):
        sitemaps.append(f"{origin}{reverse('seo_public:sitemap')}")
        if row.sitemap_news_enabled:
            sitemaps.append(f"{origin}{reverse('seo_public:sitemap_news')}")
    return Document(tuple(groups), tuple(sitemaps), origin)


def render(document: Document) -> str:
    lines = [
        f"# robots.txt generated by Mobofirm SEO (RFC 9309), policy {POLICY_VERSION}.",
        "# Longest matching rule wins; Allow wins a tie. Edit it in the SEO settings.",
    ]
    if document.origin:
        lines.append(f"# Site: {document.origin}")
    for group in document.groups:
        lines.append("")
        if group.note:
            lines.append(f"# {group.note}")
        lines.extend(f"User-agent: {agent}" for agent in group.agents)
        lines.extend(rule.line for rule in group.rules)
        if group.crawl_delay:
            lines.append(f"Crawl-delay: {group.crawl_delay}")
    if document.sitemaps:
        lines.append("")
        lines.extend(f"Sitemap: {url}" for url in document.sitemaps)
    return "\n".join(lines) + "\n"


def generate(row: SEOSettings, origin: str) -> str:
    return render(build_document(row, origin))


def build_robots(request: HttpRequest | None = None) -> str:
    """The live robots.txt body."""
    return generate(SEOSettings.get_solo(), site_origin(request))


def etag_for(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:32]


# --- parsing and matching ----------------------------------------------------------

_PERCENT = re.compile(r"%([0-9A-Fa-f]{2})")
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _normalise(value: str) -> str:
    """Compare percent-encoded octets case-insensitively; decode unreserved ones."""

    def fix(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else f"%{match.group(1).upper()}"

    return _PERCENT.sub(fix, value)


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(expression + ("$" if anchored else ""), re.DOTALL)


def _target(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return _normalise(path + (f"?{parts.query}" if parts.query else ""))


@dataclass
class ParsedRobots:
    groups: list[Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)

    def _rules_for(self, agent: str) -> tuple[str, list[Rule]]:
        token = agent.strip().lower()
        while token:
            matched = [
                group
                for group in self.groups
                if any(name.lower() == token for name in group.agents)
            ]
            if matched:
                return token, [rule for group in matched for rule in group.rules]
            token = PARENT_AGENT.get(token, "")
        star = [group for group in self.groups if "*" in group.agents]
        if star:
            return "*", [rule for group in star for rule in group.rules]
        return "", []

    def test(self, agent: str, url: str) -> Decision:
        """RFC 9309 decision for ``agent`` fetching ``url`` (path and query)."""
        token, rules = self._rules_for(agent)
        target = _target(url)
        best: Rule | None = None
        best_length = -1
        for rule in rules:
            pattern = _normalise(rule.pattern)
            if not pattern or not _pattern_regex(pattern).match(target):
                continue
            length = len(pattern)
            better_tie = length == best_length and rule.allow
            if length > best_length or (
                better_tie and best is not None and not best.allow
            ):
                best, best_length = rule, length
        return Decision(best is None or best.allow, token, best)

    def can_fetch(self, agent: str, url: str) -> bool:
        return self.test(agent, url).allowed

    def crawl_delay(self, agent: str) -> int | None:
        token = agent.strip().lower()
        for group in self.groups:
            if group.crawl_delay is not None and any(
                name.lower() == token for name in group.agents
            ):
                return group.crawl_delay
        return None


def parse(text: str | Iterable[str]) -> ParsedRobots:
    """Parse a robots.txt into groups; unknown or malformed lines are ignored."""
    lines = text.splitlines() if isinstance(text, str) else list(text)
    parsed = ParsedRobots()
    agents: list[str] = []
    rules: list[Rule] = []
    delay: int | None = None
    collecting_agents = False

    def close() -> None:
        if agents:
            parsed.groups.append(Group(tuple(agents), tuple(rules), delay))

    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        field_name, separator, value = line.partition(":")
        if not separator:
            continue
        name, value = field_name.strip().lower(), value.strip()
        if name == "user-agent":
            if not collecting_agents:
                close()
                agents, rules, delay = [], [], None
            agents.append(value)
            collecting_agents = True
            continue
        if name == "sitemap":
            parsed.sitemaps.append(value)
            continue
        collecting_agents = False
        if not agents:
            continue
        if name in {"allow", "disallow"}:
            if value:
                rules.append(Rule(name == "allow", value))
        elif name == "crawl-delay" and value.isdigit():
            delay = int(value)
    close()
    return parsed


# --- staff helpers -------------------------------------------------------------------


def diff_lines(previous: str, current: str) -> list[str]:
    """Unified diff lines between two generated robots.txt bodies (empty if equal)."""
    return list(
        difflib.unified_diff(
            previous.splitlines(),
            current.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=1,
        )
    )


def validate(body: str) -> list[str]:
    """Problems that would make crawlers ignore or truncate the file."""
    problems: list[str] = []
    if len(body.encode()) > MAX_BYTES:
        problems.append("robots.txt is larger than 500 KiB; crawlers truncate it.")
    if not parse(body).groups:
        problems.append("No User-agent group is defined.")
    return problems
