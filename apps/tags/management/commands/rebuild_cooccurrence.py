from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.blog.models import Post, PostStatus
from apps.tags.models import Tag


class Command(BaseCommand):
    help = "Rebuild tag co-occurrence and usage counts for all tags."

    def handle(self, *args, **options):
        self.stdout.write("Rebuilding tag usage and co-occurrence...")
        with transaction.atomic():
            # Reset usage
            Tag.objects.update(usage_count=0, co_occurrence={})

            # Build usage counts and co-occurrence in Python to stay portable (SQLite, Postgres).
            co_map: dict[int, dict[str, int]] = {}
            usage_map: dict[int, int] = {}

            for post in Post.objects.filter(status=PostStatus.PUBLISHED):
                tag_ids = list(post.tags.values_list("id", flat=True))
                for tid in tag_ids:
                    usage_map[tid] = usage_map.get(tid, 0) + 1
                for i, tid in enumerate(tag_ids):
                    co_map.setdefault(tid, {})
                    for other in tag_ids[i + 1 :]:
                        co_map[tid][str(other)] = co_map[tid].get(str(other), 0) + 1
                        co_map.setdefault(other, {})
                        co_map[other][str(tid)] = co_map[other].get(str(tid), 0) + 1

            # Persist in batches
            for tid, count in usage_map.items():
                Tag.objects.filter(id=tid).update(usage_count=count)
            for tid, co in co_map.items():
                Tag.objects.filter(id=tid).update(co_occurrence=co)
        self.stdout.write(self.style.SUCCESS("Co-occurrence rebuild complete."))
