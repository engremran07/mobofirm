from __future__ import annotations

from typing import Any

from rest_framework import permissions, serializers, viewsets

from apps.devices.models import BehaviorInsight, DeviceConfig, DeviceFingerprint
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    enforce_device_policy_for_login,
    enforce_device_policy_for_service,
    ensure_browser_key,
    get_active_device_count,
    get_device_limit,
    get_device_registration_summary,
    hash_browser_key,
    promote_from_device_event,
    record_insight,
    resolve_identity,
)


def get_settings() -> dict[str, Any]:
    cfg = DeviceConfig.get_solo()
    return {
        "basic_fingerprinting_enabled": bool(cfg.basic_fingerprinting_enabled),
        "enhanced_fingerprinting_enabled": bool(cfg.enhanced_fingerprinting_enabled),
        "strict_new_device_login": bool(cfg.strict_new_device_login),
        "max_devices_default": int(cfg.max_devices_default or 3),
    }


# ---------------------------------------------------------------------------
# DRF serializers + viewsets (merged from device_registry)
# ---------------------------------------------------------------------------


class DeviceFingerprintSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceFingerprint
        fields = (
            "id",
            "fingerprint_hash",
            "trust_level",
            "device_type",
            "is_bot",
            "ip",
            "last_seen",
        )
        read_only_fields = fields


class BehaviorInsightSerializer(serializers.ModelSerializer):
    class Meta:
        model = BehaviorInsight
        fields = (
            "id",
            "severity",
            "status",
            "recommendation",
            "metadata",
            "created_at",
        )
        read_only_fields = (
            "id",
            "severity",
            "recommendation",
            "metadata",
            "created_at",
        )


class DeviceFingerprintViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DeviceFingerprint.objects.all()
    serializer_class = DeviceFingerprintSerializer
    permission_classes = (permissions.IsAdminUser,)


class BehaviorInsightViewSet(viewsets.ModelViewSet):
    """Insights are produced by the system; staff may only update their status."""

    queryset = BehaviorInsight.objects.all()
    serializer_class = BehaviorInsightSerializer
    permission_classes = (permissions.IsAdminUser,)
    http_method_names = ("get", "head", "options", "patch")


__all__ = [
    "BehaviorInsightViewSet",
    "DeviceFingerprintViewSet",
    "DevicePolicyError",
    "approve_current_device",
    "enforce_device_policy_for_login",
    "enforce_device_policy_for_service",
    "ensure_browser_key",
    "get_active_device_count",
    "get_device_limit",
    "get_device_registration_summary",
    "get_settings",
    "hash_browser_key",
    "promote_from_device_event",
    "record_insight",
    "resolve_identity",
]
