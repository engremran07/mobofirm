from __future__ import annotations

from datetime import timedelta
from typing import cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.firmwares.download_service import (
    check_download_allowed,
    check_hotlink,
    complete_ad_gate,
    create_download_token,
    start_download_session,
    validate_download_token,
)
from apps.firmwares.models import DownloadToken, HotlinkBlock, OfficialFirmware
from apps.users.models import CustomUserManager


class DownloadServiceTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        user_manager = cast(CustomUserManager, user_model.objects)
        self.user = user_manager.create_user(
            username="download_user",
            email="download@example.com",
            password="test-pass-123",
        )
        self.factory = RequestFactory()
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="test_fw.zip",
            stored_file_path="/firmwares/test_fw.zip",
            uploader=self.user,
        )

    def _request(self, path: str = "/firmwares/download/"):
        request = self.factory.get(path)
        request.user = self.user
        request.META["REMOTE_ADDR"] = "198.51.100.10"
        return request

    def test_create_download_token_sets_active_status_and_ip(self) -> None:
        token = create_download_token(self.firmware, self._request())

        self.assertEqual(token.status, DownloadToken.Status.ACTIVE)
        self.assertEqual(token.ip, "198.51.100.10")
        self.assertEqual(token.user, self.user)

    def test_validate_download_token_marks_expired_tokens(self) -> None:
        token = DownloadToken.objects.create(
            firmware=self.firmware,
            user=self.user,
            token="expired-token",
            ad_gate_required=False,
            ad_gate_completed=True,
            status=DownloadToken.Status.ACTIVE,
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        validated = validate_download_token(token.token)
        token.refresh_from_db()

        self.assertIsNone(validated)
        self.assertEqual(token.status, DownloadToken.Status.EXPIRED)

    def test_complete_ad_gate_sets_completion_and_logs_event(self) -> None:
        with patch(
            "apps.firmwares.download_service._get_download_config",
            return_value={
                "gate_enabled": True,
                "countdown_seconds": 10,
                "ad_gate_enabled": True,
                "ad_gate_seconds": 30,
                "token_expiry_minutes": 30,
                "require_login": False,
                "max_per_day": 0,
                "hotlink_protection": True,
                "link_encryption": True,
            },
        ):
            token = create_download_token(self.firmware, self._request())
            start_download_session(token, self._request())
            complete_ad_gate(token, ad_type="video")

        token.refresh_from_db()
        session = token.sessions.first()  # type: ignore[attr-defined]

        self.assertTrue(token.ad_gate_completed)
        self.assertIsNotNone(session)
        self.assertEqual(session.ad_logs.count(), 1)

    def test_check_download_allowed_enforces_daily_limit(self) -> None:
        DownloadToken.objects.create(
            firmware=self.firmware,
            user=self.user,
            token="used-token",
            ad_gate_required=False,
            ad_gate_completed=True,
            status=DownloadToken.Status.USED,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        with patch(
            "apps.firmwares.download_service._get_download_config",
            return_value={
                "gate_enabled": True,
                "countdown_seconds": 10,
                "ad_gate_enabled": False,
                "ad_gate_seconds": 30,
                "token_expiry_minutes": 30,
                "require_login": False,
                "max_per_day": 1,
                "hotlink_protection": True,
                "link_encryption": True,
            },
        ):
            allowed, reason = check_download_allowed(self._request(), self.firmware)

        self.assertFalse(allowed)
        self.assertIn("Daily download limit", reason)

    def test_check_hotlink_blocks_known_external_domain(self) -> None:
        HotlinkBlock.objects.create(domain="evil.example", is_active=True)
        request = self._request()
        request.META["HTTP_REFERER"] = "https://evil.example/steal"

        with patch(
            "apps.firmwares.download_service._get_download_config",
            return_value={
                "gate_enabled": True,
                "countdown_seconds": 10,
                "ad_gate_enabled": False,
                "ad_gate_seconds": 30,
                "token_expiry_minutes": 30,
                "require_login": False,
                "max_per_day": 0,
                "hotlink_protection": True,
                "link_encryption": True,
            },
        ):
            blocked = check_hotlink(request)

        self.assertTrue(blocked)
