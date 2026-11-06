from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.seo.models import LinkableEntity
from apps.seo.services.internal_linking.engine import suggest_links


class Command(BaseCommand):
    help = "Generate link suggestions between linkable entities (simple heuristic)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=5, help="Max suggestions per entity"
        )

    def handle(self, *args, **options):
        entities = list(LinkableEntity.objects.all())
        total = 0
        for source in entities:
            suggest_links(source, entities, limit=options["limit"], force=True)
            total += 1
        self.stdout.write(
            self.style.SUCCESS(f"Suggestions generated for {total} entities")
        )
