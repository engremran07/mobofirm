"""Google News and Discover: what is real, what is manual, and what we can check.

Facts (Google's public documentation; verify against it when Google changes rules):

* Google News and Discover reach is **not automatic**. A site becomes eligible through
  Google's own systems: Publisher Center registration (News), a verified Search Console
  property, and content quality. No feed, ping or sitemap guarantees inclusion.
* What this app does for you: serves the opt-in Google News sitemap owned by ``apps.seo``
  (only genuine news posts of the last 48 hours), serves a full-text Google-News-style
  RSS/Atom feed of the same posts, and scans recent posts for the things Discover
  looks for (large image, sensible title, dates, author, canonical).
* What stays manual: registering in Publisher Center, verifying Search Console and
  submitting the URLs. Staff mark each step done on the Feeds page; the app never claims
  Google accepted anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from django.apps import apps
from django.conf import settings
from django.core.files.storage import default_storage
from django.urls import NoReverseMatch, reverse

from apps.distribution import feeds
from apps.distribution.models import DistributionSettings
from apps.seo.siteurls import absolute_url

MIN_IMAGE_WIDTH = 1200
TITLE_MIN, TITLE_MAX = 20, 110
SCAN_LIMIT = 20
STEPS = (
    (
        "publisher_center",
        "Register the publication in Google Publisher Center",
        "https://publishercenter.google.com/",
    ),
    (
        "search_console",
        "Verify the site as a Search Console property",
        "https://search.google.com/search-console",
    ),
    (
        "submitted",
        "Submit the news sitemap or feed in Publisher Center / Search Console",
        "https://support.google.com/news/publisher-center/",
    ),
)


def checklist() -> list[dict[str, Any]]:
    done = DistributionSettings.get_solo().news_checklist or {}
    return [
        {"key": key, "label": label, "url": url, "done": bool(done.get(key))}
        for key, label, url in STEPS
    ]


def set_step(key: str, done: bool) -> bool:
    if key not in {step[0] for step in STEPS}:
        return False
    cfg = DistributionSettings.get_solo()
    state = dict(cfg.news_checklist or {})
    state[key] = bool(done)
    cfg.news_checklist = state
    cfg.save(update_fields=["news_checklist"])
    return True


def seo_flags() -> dict[str, bool]:
    if not apps.is_installed("apps.seo"):
        return {"installed": False, "seo": False, "sitemap": False, "news": False}
    row = apps.get_model("seo", "SEOSettings").get_solo()
    return {
        "installed": True,
        "seo": bool(row.seo_enabled),
        "sitemap": bool(row.sitemap_enabled),
        "news": bool(row.sitemap_news_enabled),
    }


def status() -> dict[str, Any]:
    flags = seo_flags()
    try:
        sitemap_url = absolute_url(reverse("seo_public:sitemap_news"))
    except NoReverseMatch:
        sitemap_url = ""
    spec = feeds.spec_for(feeds.NEWS_KEY)
    eligible = (
        feeds.items_for(spec, 50) if spec is not None and feeds.news_available() else []
    )
    steps = checklist()
    return {
        "flags": flags,
        "active": bool(flags["news"] and flags["seo"]),
        "sitemap_url": sitemap_url,
        "rss_url": feeds.feed_url(feeds.NEWS_KEY, "rss"),
        "atom_url": feeds.feed_url(feeds.NEWS_KEY, "atom"),
        "eligible": len(eligible),
        "steps": steps,
        "registered": all(step["done"] for step in steps),
    }


# ---------------------------------------------------------- discover scan ----


@dataclass(slots=True)
class Check:
    pk: int
    title: str
    url: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    edit_url: str = ""

    @property
    def ready(self) -> bool:
        return not self.problems


def image_width(value: str) -> int | None:
    """Pixel width of a local image, or ``None`` when it cannot be measured."""
    prefix = str(getattr(settings, "MEDIA_URL", "/media/"))
    path = urlsplit(value).path
    if not path.startswith(prefix):
        return None
    try:
        from PIL import Image  # Pillow is already a dependency (share cards)

        with default_storage.open(unquote(path[len(prefix) :])) as handle:
            return int(Image.open(handle).width)
    except (OSError, ValueError, ImportError, NotImplementedError):
        return None


def _check_post(post: Any) -> Check:
    url = absolute_url(post.get_absolute_url())
    check = Check(post.pk, post.title, url)
    try:
        check.edit_url = reverse("admin:blog_post_change", args=[post.pk])
    except NoReverseMatch:
        check.edit_url = ""
    if not TITLE_MIN <= len(post.title) <= TITLE_MAX:
        check.problems.append(
            f"Title is {len(post.title)} characters; aim for {TITLE_MIN} to {TITLE_MAX}."
        )
    image = str(getattr(post, "cover_image_url", "") or "")
    if not image:
        check.problems.append(
            "No cover image served from this site (Discover wants a large image)."
        )
    else:
        width = image_width(image)
        if width is None:
            check.notes.append(
                f"Image width not measured; use at least {MIN_IMAGE_WIDTH}px."
            )
        elif width < MIN_IMAGE_WIDTH:
            check.problems.append(
                f"Image is {width}px wide; Discover wants {MIN_IMAGE_WIDTH}px or more."
            )
    if not (post.published_at or post.publish_at):
        check.problems.append("No publication date.")
    if getattr(post, "author", None) is None and not getattr(
        post, "is_ai_generated", False
    ):
        check.problems.append("No author byline.")
    canonical = str(getattr(post, "canonical_url", "") or "")
    if canonical and urlsplit(canonical).path != post.get_absolute_url():
        check.problems.append(
            "Canonical points elsewhere, so Google may not show this page."
        )
    if getattr(post, "noindex", False):
        check.problems.append("Post is set to noindex.")
    return check


def discover_scan(limit: int = SCAN_LIMIT) -> list[Check]:
    """Readiness of the newest live posts. Facts only: Discover inclusion is Google's call."""
    if not apps.is_installed("apps.blog"):
        return []
    post_model = apps.get_model("blog", "Post")
    posts = (
        post_model.published()
        .select_related("author")
        .order_by("-published_at")[:limit]
    )
    return [_check_post(post) for post in posts]


def seo_scan_url() -> str:
    try:
        return reverse("seo:issues")
    except NoReverseMatch:
        return ""
