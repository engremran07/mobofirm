"""
Firmwares API — DRF viewsets merged from dissolved apps:
  download_links, fw_scraper, fw_verification, gsmarena_sync
"""

from __future__ import annotations

from rest_framework import permissions, serializers, viewsets

from .models import (
    AdGateLog,
    ChangelogEntry,
    DownloadSession,
    DownloadToken,
    FirmwareDiff,
    GSMArenaDevice,
    HotlinkBlock,
    TestResult,
    TrustedTester,
    VerificationCredit,
    VerificationReport,
)

# ---------------------------------------------------------------------------
# Download links
# ---------------------------------------------------------------------------


class DownloadTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DownloadToken
        fields = [
            "id",
            "token",
            "firmware",
            "user",
            "status",
            "ad_gate_required",
            "expires_at",
            "created_at",
        ]
        read_only_fields = ["id", "token", "created_at"]


class DownloadSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DownloadSession
        fields = [
            "id",
            "token",
            "status",
            "bytes_delivered",
            "started_at",
            "completed_at",
        ]
        read_only_fields = ["id", "started_at"]


class HotlinkBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = HotlinkBlock
        fields = ["id", "domain", "is_active", "reason", "blocked_count"]
        read_only_fields = ["id", "blocked_count"]


class AdGateLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdGateLog
        fields = [
            "id",
            "session",
            "ad_type",
            "ad_network",
            "watched_seconds",
            "completed",
            "created_at",
        ]
        read_only_fields = fields


class DownloadTokenViewSet(viewsets.ModelViewSet):
    serializer_class = DownloadTokenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self.request.user, "is_staff", False):
            return DownloadToken.objects.all()
        return DownloadToken.objects.filter(user=self.request.user)


class DownloadSessionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DownloadSession.objects.all()
    serializer_class = DownloadSessionSerializer
    permission_classes = [permissions.IsAdminUser]


class HotlinkBlockViewSet(viewsets.ModelViewSet):
    queryset = HotlinkBlock.objects.all()
    serializer_class = HotlinkBlockSerializer
    permission_classes = [permissions.IsAdminUser]


# ---------------------------------------------------------------------------
# Firmware verification API
# ---------------------------------------------------------------------------


class TrustedTesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrustedTester
        fields = ["id", "user", "fw_count", "avg_rating", "is_active"]
        read_only_fields = fields


class VerificationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationReport
        fields = [
            "id",
            "firmware",
            "device",
            "tester",
            "verdict",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "tester", "status", "created_at"]


class TestResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestResult
        fields = ["id", "report", "test_name", "result", "notes"]
        read_only_fields = ["id"]


class VerificationCreditSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationCredit
        fields = [
            "id",
            "user",
            "firmware",
            "report",
            "credits_earned",
            "created_at",
        ]
        read_only_fields = fields


class VerificationReportViewSet(viewsets.ModelViewSet):
    queryset = VerificationReport.objects.filter(status="published")
    serializer_class = VerificationReportSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class TrustedTesterViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TrustedTester.objects.filter(is_active=True)
    serializer_class = TrustedTesterSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]


# ---------------------------------------------------------------------------
# GSMArena device synchronization
# ---------------------------------------------------------------------------


class GSMArenaDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GSMArenaDevice
        fields = ["id", "gsmarena_id", "brand", "model_name", "last_synced_at"]
        read_only_fields = fields


class GSMArenaDeviceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = GSMArenaDevice.objects.all()
    serializer_class = GSMArenaDeviceSerializer
    permission_classes = [permissions.IsAdminUser]


# ---------------------------------------------------------------------------
# Changelog API
# ---------------------------------------------------------------------------


class ChangelogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangelogEntry
        fields = ["id", "version", "title", "summary", "release_date", "is_published"]
        read_only_fields = ["id"]


class FirmwareDiffSerializer(serializers.ModelSerializer):
    class Meta:
        model = FirmwareDiff
        fields = ["id", "old_firmware", "new_firmware", "diff_text", "generated_at"]
        read_only_fields = [
            "id",
            "old_firmware",
            "new_firmware",
            "diff_text",
            "generated_at",
        ]


class ChangelogEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ChangelogEntry.objects.filter(is_published=True)
    serializer_class = ChangelogEntrySerializer
    permission_classes = [permissions.AllowAny]


class FirmwareDiffViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FirmwareDiff.objects.all()
    serializer_class = FirmwareDiffSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
