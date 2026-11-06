from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.seo import audit
from apps.seo.models import Issue


class Command(BaseCommand):
    help = "Audit the sitemap and indexation policy without any HTTP requests."

    def handle(self, *args, **options):
        findings = audit.analyze_inventory()
        audit.sync_issues(None, findings, set(), inventory=True)
        open_errors = Issue.objects.filter(
            status=Issue.Status.OPEN, severity=Issue.Severity.ERROR
        ).count()
        self.stdout.write(f"{len(findings)} findings, {open_errors} open errors.")
