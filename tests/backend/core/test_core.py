from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.management.commands.cleanup_old_data import Command
from apps.firmwares.models import DownloadSession, DownloadToken, OfficialFirmware


class CleanupOldDataCommandTests(TestCase):
    def test_cleanup_rejects_non_positive_retention_period(self):
        with self.assertRaises(CommandError):
            Command().handle(days=0, dry_run=False)

    def test_download_session_cleanup_is_scoped_and_dry_run_is_non_destructive(self):
        user = get_user_model().objects.create_user(
            username="cleanup_user",
            email="cleanup@example.com",
            password="Complex-test-password-123!",
        )
        firmware = OfficialFirmware.objects.create(
            original_file_name="cleanup-firmware.zip",
            stored_file_path="/firmwares/cleanup-firmware.zip",
            uploader=user,
        )
        token = DownloadToken.objects.create(
            firmware=firmware,
            user=user,
            token="cleanup-session-token",
            ad_gate_required=False,
            ad_gate_completed=True,
            expires_at=timezone.now() + timedelta(days=1),
        )
        old_session = DownloadSession.objects.create(
            token=token,
            user=user,
            status=DownloadSession.Status.COMPLETED,
        )
        DownloadSession.objects.filter(pk=old_session.pk).update(
            started_at=timezone.now() - timedelta(days=100)
        )
        active_session = DownloadSession.objects.create(
            token=token,
            user=user,
            status=DownloadSession.Status.STARTED,
        )
        cutoff = timezone.now() - timedelta(days=90)

        self.assertEqual(
            Command().cleanup_download_sessions(cutoff, dry_run=True),
            1,
        )
        self.assertTrue(DownloadSession.objects.filter(pk=old_session.pk).exists())

        self.assertEqual(Command().cleanup_download_sessions(cutoff), 1)
        self.assertFalse(DownloadSession.objects.filter(pk=old_session.pk).exists())
        self.assertTrue(DownloadSession.objects.filter(pk=active_session.pk).exists())
