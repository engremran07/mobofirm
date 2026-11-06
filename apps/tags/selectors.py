"""Read-side queries for public tag surfaces (the only place that defines 'public')."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.tags.models import Tag

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations


class LiveCountAnnotation(TypedDict):
    live_count: int


@dataclass(frozen=True)
class CloudTag:
    name: str
    slug: str
    count: int
    weight: str


def public_tags() -> QuerySet[Tag]:
    """Curated, active, not deleted and not merged away."""
    return Tag.objects.filter(
        is_active=True,
        is_curated=True,
        is_deleted=False,
        merge_into__isnull=True,
    )


def with_live_post_counts(
    tags: QuerySet[Tag],
) -> QuerySet[WithAnnotations[Tag, LiveCountAnnotation]]:
    """Annotate ``live_count`` with the number of published posts using each tag."""
    live = Q(posts__status="published", posts__publish_at__lte=timezone.now())
    return tags.annotate(live_count=Count("posts", filter=live, distinct=True))


def tag_cloud(limit: int = 30) -> list[CloudTag]:
    """Most used public tags with a ``weight`` bucket (``sm``/``md``/``lg``)."""
    rows = list(
        with_live_post_counts(public_tags())
        .filter(live_count__gt=0)
        .order_by("-live_count", "name")
        .values("name", "slug", "live_count")[:limit]
    )
    peak = max((row["live_count"] for row in rows), default=1)
    cloud = []
    for row in rows:
        ratio = row["live_count"] / peak
        weight = "lg" if ratio >= 0.66 else "md" if ratio >= 0.33 else "sm"
        cloud.append(CloudTag(row["name"], row["slug"], row["live_count"], weight))
    return sorted(cloud, key=lambda tag: tag.name.lower())
