from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.tags import services
from apps.tags.models_keyword import KeywordProvider


class Command(BaseCommand):
    help = "Fetch external keyword suggestions from enabled providers and store them for review."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider", type=str, help="Optional provider name to limit fetch"
        )

    def handle(self, *args, **options):
        provider_name = options.get("provider")
        qs = KeywordProvider.objects.filter(is_enabled=True)
        if provider_name:
            qs = qs.filter(name__iexact=provider_name)
        if not qs.exists():
            self.stdout.write("No enabled providers.")
            return
        for provider in qs:
            self.stdout.write(f"Fetching keywords for provider {provider.name}...")
            try:
                keywords = services.fetch_external_keywords(provider)  # type: ignore[attr-defined]
                services.store_keyword_suggestions(provider, keywords)  # type: ignore[attr-defined]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Stored {len(keywords)} suggestions for {provider.name}"
                    )
                )
            except Exception as exc:
                provider.last_status = f"error: {exc}"
                provider.save(update_fields=["last_status"])
                self.stdout.write(
                    self.style.ERROR(f"Failed for {provider.name}: {exc}")
                )
