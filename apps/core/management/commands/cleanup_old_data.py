"""
Cleanup Old Data Command
Removes old sessions, logs, temporary files, and expired data
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Clean up expired sessions and old completed firmware and ad records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Delete data older than N days (default: 90)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        cutoff = timezone.now() - timedelta(days=days)

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No data will be deleted")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Cleaning up data older than {days} days ({cutoff.date()})..."
            )
        )

        total_deleted = 0

        total_deleted += self.cleanup_sessions(dry_run)
        total_deleted += self.cleanup_api_tokens(dry_run)
        total_deleted += self.cleanup_download_sessions(cutoff, dry_run)
        total_deleted += self.cleanup_ad_events(cutoff, dry_run)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTotal records {'matched' if dry_run else 'deleted'}: {total_deleted:,}"
            )
        )

    def cleanup_api_tokens(self, dry_run=False):
        """Cleanup API tokens past their configured lifetime"""
        from rest_framework.authtoken.models import Token

        expired = Token.objects.filter(
            created__lt=timezone.now()
            - timedelta(
                seconds=getattr(settings, "API_TOKEN_TTL_SECONDS", 7 * 24 * 3600)
            )
        )
        count = expired.count()
        if count and not dry_run:
            expired.delete()
        self.stdout.write(f"  Expired API tokens: {count:,}")
        return count

    def cleanup_sessions(self, dry_run=False):
        """Cleanup expired Django sessions"""
        from django.contrib.sessions.models import Session

        expired = Session.objects.filter(expire_date__lt=timezone.now())
        count = expired.count()
        if count and not dry_run:
            expired.delete()
        self.stdout.write(f"  Sessions: {count:,} expired")
        return count

    def cleanup_download_sessions(self, cutoff, dry_run=False):
        """Cleanup old completed download sessions"""
        from apps.firmwares.models import DownloadSession

        old_sessions = DownloadSession.objects.filter(
            status=DownloadSession.Status.COMPLETED, started_at__lt=cutoff
        )
        count = old_sessions.count()
        if count and not dry_run:
            old_sessions.delete()
        self.stdout.write(f"  Completed firmware download sessions: {count:,}")
        return count

    def cleanup_ad_events(self, cutoff, dry_run=False):
        """Cleanup old ad event records"""
        from apps.ads.models import AdEvent

        old_events = AdEvent.objects.filter(created_at__lt=cutoff)
        count = old_events.count()
        if count and not dry_run:
            old_events.delete()
        self.stdout.write(f"  Ad events: {count:,}")
        return count
