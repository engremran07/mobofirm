from __future__ import annotations

from typing import Any

from rest_framework import permissions, serializers, viewsets

from apps.devices.models import BehaviorInsight, DeviceConfig, DeviceFingerprint
from apps.devices.services import (
    enforce_device_policy_for_login,
    enforce_device_policy_for_service,
    promote_from_device_event,
    record_insight,
    resolve_identity,
)


def get_settings() -> dict[str, Any]:
    try:
        cfg = DeviceConfig.get_solo()
        return {
            "basic_fingerprinting_enabled": bool(cfg.basic_fingerprinting_enabled),
            "enhanced_fingerprinting_enabled": bool(
                cfg.enhanced_fingerprinting_enabled
            ),
            "strict_new_device_login": bool(cfg.strict_new_device_login),
            "max_devices_default": int(cfg.max_devices_default or 5),
        }
    except Exception:
        return {
            "basic_fingerprinting_enabled": True,
            "enhanced_fingerprinting_enabled": False,
            "strict_new_device_login": False,
            "max_devices_default": 5,
        }


# ---------------------------------------------------------------------------
# DRF serializers + viewsets (merged from device_registry)
# ---------------------------------------------------------------------------


class DeviceFingerprintSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceFingerprint
        fields = [
            "id",
            "fingerprint_hash",
            "trust_level",
            "device_type",
            "is_bot",
            "ip",
            "last_seen",
        ]
        read_only_fields = fields


class BehaviorInsightSerializer(serializers.ModelSerializer):
    class Meta:
        model = BehaviorInsight
        fields = [
            "id",
            "severity",
            "status",
            "recommendation",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class DeviceFingerprintViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DeviceFingerprint.objects.all()
    serializer_class = DeviceFingerprintSerializer
    permission_classes = [permissions.IsAdminUser]


class BehaviorInsightViewSet(viewsets.ModelViewSet):
    queryset = BehaviorInsight.objects.all()
    serializer_class = BehaviorInsightSerializer
    permission_classes = [permissions.IsAdminUser]


__all__ = [
    "BehaviorInsightViewSet",
    "DeviceFingerprintViewSet",
    "enforce_device_policy_for_login",
    "enforce_device_policy_for_service",
    "get_settings",
    "promote_from_device_event",
    "record_insight",
    "resolve_identity",
]
