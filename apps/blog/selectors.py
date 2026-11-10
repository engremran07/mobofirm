"""Read-side queries and card assembly for the public blog."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import (
    Case,
    Count,
    F,
    FloatField,
    Q,
    QuerySet,
    TextField,
    Value,
    When,
)
from django.utils import timezone

from apps.blog.models import Category, Post, PostAccess
from apps.blog.paywall import public_excerpt
from apps.core.search import ListSearch, search_queryset
from apps.tags.models import Tag
from apps.tags.selectors import public_tags

SORT_OPTIONS = (
    ("recent", "Newest"),
    ("popular", "Most read"),
    ("oldest", "Oldest"),
    ("title", "Title A–Z"),
)
PAGE_SIZE = 12
PLACEHOLDER_VARIANTS = 5


@dataclass
class PostCard:
    """Everything a card or list row needs, resolved once per post."""

    post: Post
    url: str
    title: str
    excerpt: str
    author: str
    published: datetime | None
    reading_time: int
    category: Category | None
    tags: list[Tag] = field(default_factory=list)
    cover: str = ""
    kind: str = "Article"
    variant: int = 0
    initials: str = ""


def is_editorial(post: Post) -> bool:
    """Automated and system-authored posts carry the editorial byline."""
    return post.is_ai_generated or post.author.username == getattr(
        settings, "FIRMWARE_BLOG_AUTOMATION_USERNAME", ""
    )


def author_name(post: Post) -> str:
    """Public byline: the member's display name, or the editorial team for automation."""
    if is_editorial(post):
        return getattr(settings, "BLOG_EDITORIAL_NAME", "Mobofirm Editorial")
    return post.author.public_display_name


def _kind(post: Post) -> str:
    if post.firmware_model_id:
        return "Model"
    if post.firmware_brand_id:
        return "Brand"
    if post.category_id and post.category is not None:
        return "Chipset" if post.category.slug == "chipsets" else "Article"
    return "Article"


def _variant(post: Post) -> int:
    digest = hashlib.sha1(post.slug.encode(), usedforsecurity=False).digest()
    return digest[0] % PLACEHOLDER_VARIANTS


def initials_for(title: str) -> str:
    words = [word for word in title.replace("-", " ").split() if word[:1].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or "M"


def build_card(post: Post, tags: list[Tag] | None = None) -> PostCard:
    if tags is None:
        tags = [tag for tag in post.tags.all() if is_public_tag(tag)]
    brand = post.firmware_brand
    return PostCard(
        post=post,
        url=post.get_absolute_url(),
        title=post.title,
        excerpt=public_excerpt(post, 170),
        author=author_name(post),
        published=post.published_at,
        reading_time=post.reading_time,
        category=post.category,
        tags=tags[:3],
        cover=post.cover_image_url,
        kind=_kind(post),
        variant=_variant(post),
        initials=initials_for(brand.name if brand is not None else post.title),
    )


def is_public_tag(tag: Tag) -> bool:
    return (
        tag.is_curated
        and tag.is_active
        and not tag.is_deleted
        and tag.merge_into_id is None
    )


def build_cards(posts) -> list[PostCard]:
    return [build_card(post) for post in posts]


def live_posts() -> QuerySet[Post]:
    return (
        Post.objects.live()
        .select_related("author", "category", "firmware_brand")
        .prefetch_related("tags")
    )


@dataclass
class ListingFilters:
    q: str = ""
    category: str = ""
    tag: str = ""
    author: str = ""
    sort: str = ""

    @classmethod
    def from_request(cls, params) -> ListingFilters:
        sort = (params.get("sort") or "").strip().lower()
        return cls(
            q=(params.get("q") or "").strip()[:200],
            category=(params.get("category") or "").strip()[:120],
            tag=(params.get("tag") or "").strip()[:80],
            author=(params.get("author") or "").strip()[:150],
            sort=sort if sort in dict(SORT_OPTIONS) else "",
        )

    @property
    def is_filtered(self) -> bool:
        return bool(self.q or self.category or self.tag or self.author or self.sort)

    def query_string(self, **overrides: str) -> str:
        values = {
            "q": self.q,
            "category": self.category,
            "tag": self.tag,
            "author": self.author,
            "sort": self.sort,
        } | overrides
        return urlencode({key: value for key, value in values.items() if value})


POST_SEARCH_FIELDS = (
    "title",
    "summary",
    "open_body",
    "category__name",
    "firmware_brand__name",
    "firmware_model__name",
)
# A gated body must not decide what matches: it would reveal hidden text.
_OPEN_BODY = {
    "open_body": Case(
        When(access=PostAccess.FREE, then=F("body")),
        default=Value(""),
        output_field=TextField(),
    )
}


def search_posts(posts: QuerySet[Post], query: str | None) -> ListSearch:
    """Filter ``posts`` through the shared engine (synonyms, typo tolerance)."""
    return search_queryset(posts, query, POST_SEARCH_FIELDS, annotations=_OPEN_BODY)


def apply_search(posts: QuerySet[Post], query: str) -> tuple[QuerySet[Post], bool]:
    """Filter by ``query``; the flag says whether the result is ranked by relevance."""
    if not query:
        return posts, False
    return _ranked(search_posts(posts, query)), True


def _ranked(result: ListSearch) -> QuerySet[Post]:
    """Title phrase matches first, then summary, then everything else."""
    return result.queryset.annotate(
        rank=Case(
            When(title__icontains=result.query, then=Value(3.0)),
            When(summary__icontains=result.query, then=Value(2.0)),
            default=Value(1.0),
            output_field=FloatField(),
        )
    )


def listing(filters: ListingFilters) -> tuple[QuerySet[Post], ListSearch]:
    """The journal listing for ``filters`` and the search outcome behind it."""
    result = search_posts(live_posts(), filters.q)
    posts = _ranked(result) if filters.q else result.queryset
    if filters.category:
        posts = posts.filter(
            Q(category__slug=filters.category)
            | Q(category__parent__slug=filters.category)
        )
    if filters.tag:
        posts = posts.filter(tags__slug=filters.tag, tags__in=public_tags())
    if filters.author:
        posts = posts.filter(author__username=filters.author)
    sort = filters.sort or ("relevance" if filters.q else "recent")
    if sort == "relevance":
        return posts.order_by(F("rank").desc(), "-published_at"), result
    if sort == "popular":
        return posts.order_by("-views_count", "-published_at"), result
    if sort == "oldest":
        return posts.order_by("published_at"), result
    if sort == "title":
        return posts.order_by("title"), result
    return posts.order_by("-published_at", "-pk"), result


def filtered_posts(filters: ListingFilters) -> QuerySet[Post]:
    """The journal listing for ``filters`` (see ``listing`` for search details)."""
    return listing(filters)[0]


def lead_story() -> Post | None:
    """The featured post if one exists, otherwise the newest."""
    posts = live_posts()
    featured = posts.filter(featured=True).order_by("-published_at").first()
    return featured or posts.order_by("-published_at").first()


def category_chips(limit: int = 8) -> list[Category]:
    live = Q(posts__status="published", posts__publish_at__lte=timezone.now())
    return list(
        Category.objects.filter(parent__isnull=True)
        .annotate(post_count=Count("posts", filter=live))
        .filter(post_count__gt=0)
        .order_by("-post_count", "name")[:limit]
    )


def trending_posts(limit: int = 5, exclude_pk: int | None = None) -> list[Post]:
    posts = live_posts().order_by("-views_count", "-published_at")
    if exclude_pk:
        posts = posts.exclude(pk=exclude_pk)
    return list(posts[:limit])


def latest_posts(limit: int = 5, exclude_pk: int | None = None) -> list[Post]:
    posts = live_posts().order_by("-published_at", "-pk")
    if exclude_pk:
        posts = posts.exclude(pk=exclude_pk)
    return list(posts[:limit])


def related_posts(post: Post, limit: int = 3) -> list[Post]:
    """Posts sharing tags, falling back to the same brand or category."""
    pool = live_posts().exclude(pk=post.pk)
    tag_ids = [tag.pk for tag in post.tags.all() if is_public_tag(tag)]
    chosen: list[Post] = []
    seen = {post.pk}

    def take(queryset: QuerySet[Post]) -> None:
        for candidate in queryset.order_by("-published_at")[: limit * 2]:
            if candidate.pk not in seen and len(chosen) < limit:
                seen.add(candidate.pk)
                chosen.append(candidate)

    if tag_ids:
        take(pool.filter(tags__in=tag_ids).distinct())
    if len(chosen) < limit and post.firmware_brand_id:
        take(pool.filter(firmware_brand_id=post.firmware_brand_id))
    if len(chosen) < limit and post.category_id:
        take(pool.filter(category_id=post.category_id))
    return chosen


def adjacent_posts(post: Post) -> tuple[Post | None, Post | None]:
    """The previous (older) and next (newer) live posts by publication time."""
    if not post.published_at:
        return None, None
    base = live_posts()
    older = (
        base.filter(published_at__lt=post.published_at)
        .order_by("-published_at")
        .first()
    )
    newer = (
        base.filter(published_at__gt=post.published_at).order_by("published_at").first()
    )
    return older, newer
