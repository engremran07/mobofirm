"""
Firmwares API — DRF viewsets merged from dissolved apps:
  download_links, fw_scraper, fw_verification
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework import permissions, serializers, viewsets

from apps.users.request_user import current_user

from .models import (
    AdGateLog,
    ChangelogEntry,
    DownloadSession,
    DownloadToken,
    FirmwareDiff,
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
    token_prefix = serializers.SerializerMethodField()
    firmware_type = serializers.CharField(source="content_type.model", read_only=True)
    firmware_id = serializers.UUIDField(source="object_id", read_only=True)

    class Meta:
        model = DownloadToken
        fields: ClassVar[list[str]] = [
            "id",
            "token_prefix",
            "firmware_type",
            "firmware_id",
            "user",
            "status",
            "ad_gate_required",
            "ad_gate_completed",
            "expires_at",
            "used_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_token_prefix(self, obj: DownloadToken) -> str:
        # The full token is a bearer secret and is never returned by the API.
        return f"{obj.token[:8]}…"


class DownloadSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DownloadSession
        fields: ClassVar[list[str]] = [
            "id",
            "token",
            "status",
            "bytes_delivered",
            "started_at",
            "completed_at",
        ]
        read_only_fields = ("id", "started_at")


class HotlinkBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = HotlinkBlock
        fields = ("id", "domain", "is_active", "reason", "blocked_count")
        read_only_fields = ("id", "blocked_count")


class AdGateLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdGateLog
        fields: ClassVar[list[str]] = [
            "id",
            "session",
            "ad_type",
            "ad_network",
            "watched_seconds",
            "completed",
            "created_at",
        ]
        read_only_fields = fields


class DownloadTokenViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view of download tokens; tokens are issued only by the download page."""

    serializer_class = DownloadTokenSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        queryset = DownloadToken.objects.select_related("content_type")
        user = self.request.user
        if getattr(user, "is_staff", False):
            return queryset
        if isinstance(user, AnonymousUser):
            return queryset.none()
        return queryset.filter(user=user)


class DownloadSessionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DownloadSession.objects.all()
    serializer_class = DownloadSessionSerializer
    permission_classes = (permissions.IsAdminUser,)


class HotlinkBlockViewSet(viewsets.ModelViewSet):
    queryset = HotlinkBlock.objects.all()
    serializer_class = HotlinkBlockSerializer
    permission_classes = (permissions.IsAdminUser,)


# ---------------------------------------------------------------------------
# Firmware verification API
# ---------------------------------------------------------------------------


class TrustedTesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrustedTester
        fields = ("id", "user", "fw_count", "avg_rating", "is_active")
        read_only_fields = fields


class VerificationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationReport
        fields: ClassVar[list[str]] = [
            "id",
            "firmware",
            "device",
            "tester",
            "verdict",
            "status",
            "created_at",
        ]
        read_only_fields = ("id", "tester", "status", "created_at")

    def validate_firmware(self, firmware):
        if not (firmware.is_active and firmware.is_verified):
            raise serializers.ValidationError("Firmware is not publicly listed.")
        return firmware


class TestResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestResult
        fields = ("id", "report", "test_name", "result", "notes")
        read_only_fields = ("id",)


class VerificationCreditSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationCredit
        fields: ClassVar[list[str]] = [
            "id",
            "user",
            "firmware",
            "report",
            "credits_earned",
            "created_at",
        ]
        read_only_fields = fields


class IsReportOwnerOrStaff(permissions.BasePermission):
    """Reads are public; only the tester (while submitted) or staff may change a report."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.is_staff:
            return True
        return (
            obj.tester.user_id == request.user.pk
            and obj.status == VerificationReport.Status.SUBMITTED
        )


class VerificationReportViewSet(viewsets.ModelViewSet):
    serializer_class = VerificationReportSerializer
    permission_classes = (IsReportOwnerOrStaff,)

    def get_queryset(self):
        queryset = VerificationReport.objects.select_related("tester__user")
        user = self.request.user
        if isinstance(user, AnonymousUser):
            return queryset.filter(status=VerificationReport.Status.PUBLISHED)
        if user.is_staff:
            return queryset
        return queryset.filter(
            Q(status=VerificationReport.Status.PUBLISHED) | Q(tester__user=user)
        )

    def perform_create(self, serializer):
        tester = TrustedTester.objects.filter(
            user=current_user(self.request), is_active=True
        ).first()
        if tester is None:
            raise PermissionDenied("Only active trusted testers can submit reports.")
        serializer.save(tester=tester, status=VerificationReport.Status.SUBMITTED)


class TrustedTesterViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TrustedTester.objects.filter(is_active=True)
    serializer_class = TrustedTesterSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)


# ---------------------------------------------------------------------------
# Changelog API
# ---------------------------------------------------------------------------


class ChangelogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangelogEntry
        fields = (
            "id",
            "version",
            "title",
            "summary",
            "release_date",
            "is_published",
        )
        read_only_fields = ("id",)


class FirmwareDiffSerializer(serializers.ModelSerializer):
    class Meta:
        model = FirmwareDiff
        fields = ("id", "old_firmware", "new_firmware", "diff_text", "generated_at")
        read_only_fields: ClassVar[list[str]] = [
            "id",
            "old_firmware",
            "new_firmware",
            "diff_text",
            "generated_at",
        ]


class ChangelogEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ChangelogEntry.objects.filter(is_published=True)
    serializer_class = ChangelogEntrySerializer
    permission_classes = (permissions.AllowAny,)


class FirmwareDiffViewSet(viewsets.ReadOnlyModelViewSet):
    """Diffs between publicly listed official firmware only."""

    queryset = FirmwareDiff.objects.filter(
        old_firmware__is_active=True,
        old_firmware__is_verified=True,
        new_firmware__is_active=True,
        new_firmware__is_verified=True,
    )
    serializer_class = FirmwareDiffSerializer
    permission_classes = (permissions.AllowAny,)
