from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.seo import entity_graph


class Command(BaseCommand):
    help = "Rebuild the SEO entity graph from real catalogue and blog relations."

    def handle(self, *args, **options):
        counts = entity_graph.rebuild()
        self.stdout.write(f"{counts['entities']} entities, {counts['edges']} edges.")
