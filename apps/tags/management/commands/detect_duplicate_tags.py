from __future__ import annotations

from difflib import SequenceMatcher

from django.core.management.base import BaseCommand

from apps.tags.models import Tag


def jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Command(BaseCommand):
    help = "Detect duplicate/near-duplicate tags based on normalized name similarity."

    def add_arguments(self, parser):
        parser.add_argument(
            "--threshold", type=float, default=0.7, help="Similarity threshold (0-1)"
        )
        parser.add_argument(
            "--limit", type=int, default=200, help="Max pairs to output"
        )

    def handle(self, *args, **options):
        threshold = options["threshold"]
        limit = options["limit"]
        tags = list(Tag.objects.filter(is_active=True, is_deleted=False))
        out = []
        for i, t1 in enumerate(tags):
            for t2 in tags[i + 1 :]:
                sim = max(
                    SequenceMatcher(
                        None, t1.normalized_name, t2.normalized_name
                    ).ratio(),
                    jaccard(t1.normalized_name, t2.normalized_name),
                )
                if sim >= threshold:
                    out.append((sim, t1.name, t2.name))
                    if len(out) >= limit:
                        break
            if len(out) >= limit:
                break
        out.sort(reverse=True, key=lambda x: x[0])
        for sim, a, b in out:
            self.stdout.write(f"{sim:.2f}: {a} <-> {b}")
