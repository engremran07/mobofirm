from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.devices.admin import DeviceAdmin
from apps.devices.models import (
    AppPolicy,
    BehaviorInsight,
    Device,
    DeviceConfig,
    DeviceEvent,
)
from apps.devices.models_quota import UserDeviceQuota
from apps.devices.services import (
    DevicePolicyError,
    enforce_device_policy_for_login,
    get_device_limit,
    hash_browser_key,
    mark_device_trusted,
    promote_from_device_event,
    record_insight,
    resolve_or_create_device,
)
from apps.devices.tasks import purge_old_device_events

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537"


class DeviceEnforcementBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            email="enforce@example.com", username="enforce", password="x-Valid-pass-1!"
        )
        self.factory = RequestFactory()

    def request(self, key: str, ip: str = "203.0.113.10"):
        request = self.factory.get("/", HTTP_USER_AGENT=UA)
        request.COOKIES["mobofirm_device"] = key
        request.user = self.user
        request.session = SessionStore()
        request.META["REMOTE_ADDR"] = ip
        return request

    def register(self, key: str, *, trusted: bool = False, ip: str = "203.0.113.10"):
        device, _, _ = resolve_or_create_device(self.request(key, ip), self.user)
        assert device is not None
        if trusted:
            Device.objects.filter(pk=device.pk).update(is_trusted=True)
            device.refresh_from_db()
        return device

    def configure(self, **fields):
        config = DeviceConfig.get_solo()
        for name, value in fields.items():
            setattr(config, name, value)
        config.save()


class RegistrationQuotaTests(DeviceEnforcementBase):
    def test_monthly_quota_rejects_new_registrations_without_creating_a_device(self):
        self.configure(monthly_device_quota=1)
        self.register("a" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            self.register("b" * 43)

        self.assertEqual(error.exception.reason, "monthly_device_quota")
        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)

    def test_quota_window_ignores_registrations_outside_the_period(self):
        self.configure(monthly_device_quota=1)
        old = self.register("a" * 43)
        Device.objects.filter(pk=old.pk).update(
            first_seen_at=timezone.now() - timedelta(days=45)
        )

        self.assertTrue(self.register("b" * 43).pk)

    def test_yearly_quota_is_enforced(self):
        self.configure(yearly_device_quota=2)
        self.register("a" * 43)
        self.register("b" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            self.register("c" * 43)

        self.assertEqual(error.exception.reason, "yearly_device_quota")

    def test_existing_browser_can_still_sign_in_when_the_quota_is_exhausted(self):
        self.configure(monthly_device_quota=1)
        first = self.register("a" * 43)

        again, is_new, _ = resolve_or_create_device(self.request("a" * 43), self.user)

        self.assertFalse(is_new)
        assert again is not None
        self.assertEqual(again.pk, first.pk)

    @override_settings(DEVICE_MAX_PENDING_PER_USER=2)
    def test_pending_registrations_are_capped(self):
        self.register("a" * 43)
        self.register("b" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            self.register("c" * 43)

        self.assertEqual(error.exception.reason, "limit_reached")

    def test_per_user_override_changes_the_limit_and_window_quota(self):
        UserDeviceQuota.objects.create(user=self.user, max_devices=1, window="3m")
        self.assertEqual(get_device_limit(self.user), 1)
        self.register("a" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            self.register("b" * 43)

        self.assertEqual(error.exception.reason, "user_window_quota")

    def test_superusers_are_exempt_from_registration_quotas(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        UserDeviceQuota.objects.create(user=self.user, max_devices=1, window="3m")
        self.configure(monthly_device_quota=1)

        self.register("a" * 43)
        self.register("b" * 43)

        self.assertEqual(Device.objects.filter(user=self.user).count(), 2)

    def test_login_policy_reports_the_quota_reason_and_logs_the_denial(self):
        self.configure(monthly_device_quota=1)
        self.register("a" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            enforce_device_policy_for_login(self.request("z" * 43), self.user)

        self.assertEqual(error.exception.reason, "monthly_device_quota")
        self.assertTrue(
            DeviceEvent.objects.filter(
                user=self.user, event_type="login", success=False
            ).exists()
        )


class NewDevicePolicyTests(DeviceEnforcementBase):
    def test_strict_mode_blocks_a_new_browser_once_a_trusted_one_exists(self):
        self.configure(strict_new_device_login=True)
        self.register("a" * 43, trusted=True)

        with self.assertRaises(DevicePolicyError) as error:
            enforce_device_policy_for_login(self.request("b" * 43), self.user)

        self.assertEqual(error.exception.reason, "untrusted_new_device")

    def test_strict_mode_lets_the_first_ever_browser_through(self):
        self.configure(strict_new_device_login=True)

        allowed, result = enforce_device_policy_for_login(
            self.request("a" * 43), self.user
        )

        self.assertTrue(allowed)
        self.assertTrue(result["is_new"])

    def test_app_policy_strict_locking_mode_is_enforced(self):
        AppPolicy.objects.update_or_create(
            name="default", defaults={"device_locking_mode": "strict"}
        )
        self.register("a" * 43, trusted=True)

        with self.assertRaises(DevicePolicyError) as error:
            enforce_device_policy_for_login(self.request("b" * 43), self.user)

        self.assertEqual(error.exception.reason, "untrusted_new_device")

    def test_app_policy_always_mfa_requires_a_verified_second_factor(self):
        AppPolicy.objects.update_or_create(
            name="default", defaults={"mfa_requirement": "always"}
        )
        request = self.request("a" * 43)

        with self.assertRaises(DevicePolicyError) as error:
            enforce_device_policy_for_login(request, self.user)
        allowed, _ = enforce_device_policy_for_login(
            self.request("a" * 43), self.user, mfa_verified=True
        )

        self.assertEqual(error.exception.reason, "mfa_required")
        self.assertTrue(allowed)

    def test_trust_expires_after_the_configured_number_of_idle_days(self):
        self.configure(device_expiry_days=30)
        device = self.register("a" * 43, trusted=True)
        Device.objects.filter(pk=device.pk).update(
            last_seen_at=timezone.now() - timedelta(days=45)
        )

        refreshed, _, _ = resolve_or_create_device(self.request("a" * 43), self.user)

        assert refreshed is not None
        self.assertFalse(refreshed.is_trusted)
        self.assertTrue(
            DeviceEvent.objects.filter(
                device=device, reason="trust_expired", success=False
            ).exists()
        )

    def test_risk_score_is_recomputed_instead_of_growing_forever(self):
        device = self.register("a" * 43, ip="203.0.113.1")
        for index in range(10):
            DeviceEvent.objects.create(
                user=self.user,
                device=device,
                event_type="login",
                ip=f"198.51.100.{index}",
            )
            _, _, context = resolve_or_create_device(
                self.request("a" * 43, ip=f"203.0.113.{index + 20}"), self.user
            )

        device.refresh_from_db()
        self.assertLessEqual(device.risk_score, 30)
        self.assertEqual(context["risk_score"], device.risk_score)


class MarkTrustedTests(DeviceEnforcementBase):
    def test_marking_trusted_respects_the_device_limit_and_writes_an_event(self):
        self.configure(max_devices_default=1)
        first = self.register("a" * 43)
        second = self.register("b" * 43)

        self.assertTrue(mark_device_trusted(first.pk, self.user.pk))
        self.assertFalse(mark_device_trusted(second.pk, self.user.pk))

        second.refresh_from_db()
        self.assertFalse(second.is_trusted)
        self.assertTrue(
            DeviceEvent.objects.filter(
                device=first, event_type="device_trusted"
            ).exists()
        )

    def test_revoked_devices_cannot_be_trusted(self):
        device = self.register("a" * 43)
        Device.objects.filter(pk=device.pk).update(revoked_at=timezone.now())

        self.assertFalse(mark_device_trusted(device.pk, self.user.pk))


class BehaviorInsightTests(DeviceEnforcementBase):
    def test_record_insight_stores_identifier_and_ip_in_metadata(self):
        insight = record_insight(
            related_user=self.user,
            device_identifier="device-1",
            related_ip="203.0.113.4",
            severity="high",
            recommendation="Review",
            metadata={"reason": "x"},
        )

        insight.refresh_from_db()
        self.assertEqual(insight.metadata["device_identifier"], "device-1")
        self.assertEqual(insight.metadata["ip"], "203.0.113.4")
        self.assertEqual(insight.metadata["reason"], "x")

    def test_failed_events_create_insights_and_successes_do_not(self):
        failed = promote_from_device_event(
            {
                "device_identifier": "d",
                "ip": "203.0.113.4",
                "user": self.user,
                "event_type": "login",
                "success": False,
                "reason": "blocked_device",
            }
        )
        succeeded = promote_from_device_event(
            {"user": self.user, "success": True, "reason": "policy_pass"}
        )

        assert failed is not None
        self.assertEqual(failed.severity, "high")
        self.assertIsNone(succeeded)
        self.assertEqual(BehaviorInsight.objects.count(), 1)

    def test_a_denied_login_records_an_insight_end_to_end(self):
        device = self.register("a" * 43)
        Device.objects.filter(pk=device.pk).update(is_blocked=True)

        with self.assertRaises(DevicePolicyError):
            enforce_device_policy_for_login(self.request("a" * 43), self.user)

        insight = BehaviorInsight.objects.get(related_user=self.user)
        self.assertEqual(insight.metadata["device_identifier"], str(device.pk))
        self.assertEqual(insight.metadata["reason"], "blocked_device")

    def test_successful_logins_do_not_flood_the_insight_table(self):
        enforce_device_policy_for_login(self.request("a" * 43), self.user)

        self.assertFalse(BehaviorInsight.objects.exists())

    def test_staff_may_only_update_the_status_of_an_insight(self):
        staff = get_user_model().objects.create_user(
            email="insight-staff@example.com",
            username="insightstaff",
            password="x-Valid-pass-1!",
            is_staff=True,
        )
        insight = record_insight(related_user=self.user, severity="low")
        client = APIClient()
        client.force_authenticate(staff)
        url = reverse("devices:behavior-insights-detail", args=[insight.pk])

        patched = client.patch(
            url, {"status": "resolved", "severity": "critical"}, format="json"
        )
        created = client.post(
            reverse("devices:behavior-insights-list"),
            {"status": "open"},
            format="json",
        )

        insight.refresh_from_db()
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(insight.status, "resolved")
        self.assertEqual(insight.severity, "low")
        self.assertEqual(created.status_code, 405)


class DeviceAdminAuditTests(DeviceEnforcementBase):
    def setUp(self):
        super().setUp()
        self.admin = DeviceAdmin(Device, admin.site)
        self.staff = get_user_model().objects.create_superuser(
            email="device-admin@example.com",
            username="deviceadmin",
            password="x-Valid-pass-1!",
        )

    def admin_request(self):
        request = self.factory.post("/admin/")
        request.user = self.staff
        return request

    def test_block_unblock_and_revoke_each_write_an_audit_event(self):
        device = self.register("a" * 43)
        queryset = Device.objects.filter(pk=device.pk)

        with patch.object(self.admin, "message_user"):
            self.admin.block_devices(self.admin_request(), queryset)
            self.admin.unblock_devices(self.admin_request(), queryset)
            self.admin.revoke_devices(self.admin_request(), queryset)

        reasons = set(
            DeviceEvent.objects.filter(device=device)
            .exclude(reason="")
            .values_list("reason", flat=True)
        )
        self.assertTrue(
            {"blocked_by_admin", "unblocked_by_admin", "revoked_by_admin"} <= reasons
        )
        event = DeviceEvent.objects.get(device=device, reason="blocked_by_admin")
        self.assertEqual(event.metadata["admin_user_id"], self.staff.pk)

    def test_mark_trusted_action_cannot_bypass_the_device_limit(self):
        self.configure(max_devices_default=1)
        first = self.register("a" * 43)
        second = self.register("b" * 43)

        with patch.object(self.admin, "message_user") as message_user:
            self.admin.mark_trusted(
                self.admin_request(),
                Device.objects.filter(pk__in=[first.pk, second.pk]),
            )

        trusted = Device.objects.filter(user=self.user, is_trusted=True).count()
        self.assertEqual(trusted, 1)
        self.assertIn("1 device(s) marked as trusted", message_user.call_args.args[1])


class DeviceRetentionTests(DeviceEnforcementBase):
    @override_settings(DEVICE_EVENT_RETENTION_DAYS=30)
    def test_purge_task_removes_only_events_past_retention(self):
        old = DeviceEvent.objects.create(
            user=self.user, event_type="login", ip="1.1.1.1"
        )
        recent = DeviceEvent.objects.create(
            user=self.user, event_type="login", ip="2.2.2.2"
        )
        DeviceEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=31)
        )

        result = purge_old_device_events.apply().get()

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(DeviceEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(DeviceEvent.objects.filter(pk=recent.pk).exists())

    @override_settings(DEVICE_EVENT_RETENTION_DAYS=0)
    def test_retention_can_be_disabled_explicitly(self):
        result = purge_old_device_events.apply().get()

        self.assertEqual(result, {"status": "disabled", "deleted": 0})


class LoginSignalTests(DeviceEnforcementBase):
    def test_rejected_registration_does_not_break_the_login_signal(self):
        from django.contrib.auth.signals import user_logged_in

        self.configure(monthly_device_quota=1)
        self.register("a" * 43)

        user_logged_in.send(
            sender=type(self.user), request=self.request("b" * 43), user=self.user
        )

        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)

    def test_browser_key_hash_is_what_is_stored(self):
        device = self.register("k" * 43)

        self.assertEqual(device.browser_key_hash, hash_browser_key("k" * 43))
