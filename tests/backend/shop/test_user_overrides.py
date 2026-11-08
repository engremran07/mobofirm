"""Account-level quota overrides: precedence, each limit, expiry, audit and the admin."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.shop import entitlements, overrides, services
from apps.shop.models import (
    PackageQuotaOverride,
    PackageTier,
    QuotaConsumption,
    QuotaOverrideAudit,
    UserPackage,
    UserQuotaOverride,
    WindowKind,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_tier(default_tier: PackageTier) -> PackageTier:
    PackageTier.objects.filter(pk=default_tier.pk).update(
        limit_files=2, fair_monthly_files=0, device_limit=3
    )
    default_tier.refresh_from_db()
    return default_tier


def grant(user, staff, **limits):
    return overrides.grant_override(user, actor=staff, reason="Goodwill", **limits)


def serve(user, firmware) -> bool:
    decision = entitlements.check_quota(user, firmware)
    if decision.allowed:
        entitlements.consume_quota(user, firmware, reason=firmware.access_tier)
    return decision.allowed


class TestPrecedence:
    def test_the_free_default_applies_without_an_override(self, user, free_tier):
        assert entitlements.effective_entitlements(user).download_quota == 2

    def test_an_account_override_beats_the_free_default(self, user, staff, free_tier):
        grant(user, staff, limit_files=30)

        ent = entitlements.effective_entitlements(user)

        assert ent.download_quota == 30
        assert ent.limits.files == 30
        assert any("Account override (Goodwill)" in source for source in ent.sources)

    def test_it_beats_the_package_and_the_package_override(
        self, user, staff, free_tier, pro_tier
    ):
        held = services.activate_package(user, pro_tier)
        PackageQuotaOverride.objects.create(
            user_package=held, limit_files=50, granted_by=staff
        )
        assert entitlements.effective_entitlements(user).download_quota == 50

        grant(user, staff, limit_files=7)

        assert entitlements.effective_entitlements(user).download_quota == 7

    def test_empty_fields_inherit_the_package(self, user, staff, free_tier):
        grant(user, staff, device_limit=9)

        ent = entitlements.effective_entitlements(user)

        assert ent.device_limit == 9
        assert ent.download_quota == 2

    def test_zero_means_unlimited(self, user, staff, free_tier):
        grant(user, staff, limit_files=0, device_limit=0)

        ent = entitlements.effective_entitlements(user)

        assert ent.unlimited and ent.download_quota is None
        assert ent.device_limit is None

    def test_data_volume_is_converted_to_megabytes(self, user, staff, free_tier):
        grant(user, staff, limit_files=30, limit_size=20, size_unit="GB")

        limits = entitlements.effective_entitlements(user).limits

        assert (limits.files, limits.size_mb) == (30, 20 * 1024)

    def test_the_window_can_be_changed(self, user, staff, free_tier):
        grant(user, staff, limit_files=30, window=WindowKind.MONTH)

        ent = entitlements.effective_entitlements(user)

        assert ent.window == WindowKind.MONTH
        assert ent.limits.window == WindowKind.MONTH
        assert ent.resets_at is not None and ent.resets_at.day == 1

    def test_expired_and_revoked_overrides_are_ignored(self, user, staff, free_tier):
        override = grant(user, staff, limit_files=30)
        UserQuotaOverride.objects.filter(pk=override.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        assert entitlements.effective_entitlements(user).download_quota == 2

        UserQuotaOverride.objects.filter(pk=override.pk).update(
            expires_at=None, is_active=False
        )
        assert entitlements.effective_entitlements(user).download_quota == 2

    def test_other_accounts_are_untouched(self, user, other, staff, free_tier):
        grant(user, staff, limit_files=30)

        assert entitlements.effective_entitlements(other).download_quota == 2


class TestEachLimitChangesBehaviour:
    def test_the_file_limit_blocks_and_allows_downloads(
        self, user, staff, free_tier, make_firmware
    ):
        files = [make_firmware() for _ in range(4)]
        assert [
            serve(user, files[0]),
            serve(user, files[1]),
            serve(user, files[2]),
        ] == [
            True,
            True,
            False,
        ]

        grant(user, staff, limit_files=4)

        assert serve(user, files[2]) is True
        assert serve(user, files[3]) is True
        assert QuotaConsumption.objects.filter(user=user).count() == 4

    def test_a_tighter_override_blocks_sooner(
        self, user, staff, free_tier, make_firmware
    ):
        grant(user, staff, limit_files=1)
        first, second = make_firmware(), make_firmware()

        assert serve(user, first) is True
        assert serve(user, second) is False

    def test_the_data_volume_limit_blocks_a_big_file(
        self, user, staff, free_tier, make_firmware
    ):
        grant(user, staff, limit_files=0, limit_size=20, size_unit="MB")
        small, big = make_firmware(size_mb=15), make_firmware(size_mb=10)

        assert serve(user, small) is True
        decision = entitlements.check_quota(user, big)

        assert not decision.allowed and "Size allowance" in decision.reason

    def test_unlimited_removes_the_cap(self, user, staff, free_tier, make_firmware):
        grant(user, staff, limit_files=0)

        assert all(serve(user, make_firmware()) for _ in range(6))

    def test_featured_files_follow_the_item_type_switch(
        self, user, staff, free_tier, make_firmware
    ):
        featured = make_firmware("featured")
        decision = entitlements.check_quota(user, featured)
        assert not decision.allowed and "package" in decision.reason

        grant(user, staff, covers_featured=True)

        assert serve(user, featured) is True

    def test_turning_featured_off_removes_the_package_access(
        self, user, staff, free_tier, pro_tier, make_firmware
    ):
        services.activate_package(user, pro_tier)
        featured = make_firmware("featured")
        assert entitlements.check_quota(user, featured).allowed

        grant(user, staff, covers_featured=False)

        assert not entitlements.check_quota(user, featured).allowed


class TestValidation:
    def test_it_needs_a_reason_and_a_change(self, user, staff):
        with pytest.raises(ValidationError) as error:
            UserQuotaOverride(user=user, granted_by=staff, reason=" ").full_clean(
                exclude=["user", "granted_by"]
            )

        assert {"reason", "limit_files"} <= set(error.value.message_dict)

    def test_the_expiry_must_be_in_the_future(self, user, staff):
        with pytest.raises(ValidationError) as error:
            UserQuotaOverride(
                user=user,
                reason="x",
                limit_files=1,
                expires_at=timezone.now() - timedelta(days=1),
            ).full_clean(exclude=["user", "granted_by"])

        assert "expires_at" in error.value.message_dict

    def test_only_one_override_can_be_active(self, user, staff, free_tier):
        grant(user, staff, limit_files=5)

        with pytest.raises(overrides.OverrideError, match="already has an active"):
            grant(user, staff, limit_files=6)

    def test_an_expired_override_makes_room_for_a_new_one(self, user, staff, free_tier):
        old = grant(user, staff, limit_files=5)
        UserQuotaOverride.objects.filter(pk=old.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        new = grant(user, staff, limit_files=6)

        old.refresh_from_db()
        assert not old.is_active and new.is_active

    def test_unknown_fields_are_refused(self, user, staff):
        with pytest.raises(overrides.OverrideError):
            grant(user, staff, limit_gigabytes=3)


class TestAudit:
    def test_grant_and_revoke_are_recorded_with_the_actor(self, user, staff, free_tier):
        override = grant(user, staff, limit_files=30)
        overrides.revoke_override(override, actor=staff)
        overrides.revoke_override(override, actor=staff)

        rows = list(QuotaOverrideAudit.objects.filter(user=user).order_by("id"))
        assert [row.action for row in rows] == ["granted", "revoked"]
        assert rows[0].actor == staff and rows[0].reason == "Goodwill"
        assert rows[0].snapshot["limit_files"] == 30
        assert rows[0].snapshot["summary"].startswith("30 files")

    def test_the_audit_survives_deleting_the_override(self, user, staff, free_tier):
        override = grant(user, staff, limit_files=30)

        override.delete()

        assert QuotaOverrideAudit.objects.filter(user=user).count() == 1


class TestAdmin:
    @pytest.fixture
    def root(self, staff) -> Client:
        client = Client()
        client.force_login(staff)
        return client

    def form(self, user, **extra) -> dict[str, str]:
        data = {
            "user": str(user.pk),
            "reason": "Beta tester",
            "is_active": "on",
            "limit_files": "30",
            "limit_size": "20",
            "size_unit": "GB",
            "window": "month",
            "covers_free": "",
            "covers_featured": "",
            "covers_paid": "",
            "device_limit": "",
            "expires_at_0": "",
            "expires_at_1": "",
            "granted_by": "",
        }
        data.update(extra)
        return data

    def test_the_add_form_is_tabbed_and_explains_precedence(self, root):
        page = root.get(reverse("admin:shop_userquotaoverride_add"))
        text = page.content.decode()

        assert page.status_code == 200
        assert (
            "beats the account&#x27;s package" in text
            or "beats the account's package" in text
        )
        for name in (
            "limit_files",
            "limit_size",
            "window",
            "covers_featured",
            "device_limit",
        ):
            assert f'name="{name}"' in text

    def test_creating_one_sets_granted_by_audits_and_applies(
        self, root, staff, user, free_tier
    ):
        response = root.post(
            reverse("admin:shop_userquotaoverride_add"), self.form(user)
        )

        assert response.status_code == 302
        override = UserQuotaOverride.objects.get(user=user)
        assert override.granted_by == staff
        assert override.summary() == "30 files / 20 GB (calendar month)"
        ent = entitlements.effective_entitlements(user)
        assert ent.limits.files == 30 and ent.limits.size_mb == 20 * 1024
        assert QuotaOverrideAudit.objects.filter(
            user=user, action="granted", actor=staff
        ).exists()

    def test_invalid_input_explains_the_problem(self, root, user):
        response = root.post(
            reverse("admin:shop_userquotaoverride_add"),
            self.form(user, reason="", limit_files="", limit_size="", window=""),
        )

        text = response.content.decode()
        assert response.status_code == 200
        assert "Explain why this account is an exception." in text
        assert not UserQuotaOverride.objects.exists()

    def test_a_second_active_override_is_rejected_in_the_form(
        self, root, staff, user, free_tier
    ):
        grant(user, staff, limit_files=5)

        response = root.post(
            reverse("admin:shop_userquotaoverride_add"), self.form(user)
        )

        assert response.status_code == 200
        assert "already has an active override" in response.content.decode()

    def test_the_revoke_action_switches_it_off_and_audits(
        self, root, staff, user, free_tier
    ):
        override = grant(user, staff, limit_files=30)

        root.post(
            reverse("admin:shop_userquotaoverride_changelist"),
            {"action": "revoke_selected", "_selected_action": [override.pk]},
        )

        override.refresh_from_db()
        assert not override.is_active
        assert entitlements.effective_entitlements(user).download_quota == 2
        assert QuotaOverrideAudit.objects.filter(user=user, action="revoked").exists()

    def test_the_lists_render_and_the_audit_is_read_only(
        self, root, staff, user, free_tier
    ):
        grant(user, staff, limit_files=30)

        listing = root.get(reverse("admin:shop_userquotaoverride_changelist"))
        audit = root.get(reverse("admin:shop_quotaoverrideaudit_changelist"))
        add = root.get(reverse("admin:shop_quotaoverrideaudit_add"))

        assert listing.status_code == 200 and b"30 files" in listing.content
        assert audit.status_code == 200
        assert add.status_code == 403

    def test_without_the_permission_the_pages_are_forbidden(self, make_user):
        clerk = make_user("override_clerk")
        clerk.is_staff = True
        clerk.save()
        client = Client()
        client.force_login(clerk)

        response = client.get(reverse("admin:shop_userquotaoverride_changelist"))

        assert response.status_code == 403


def test_an_account_override_survives_a_package_purchase(
    user, staff, free_tier, pro_tier
):
    grant(user, staff, device_limit=8)

    services.activate_package(user, pro_tier)

    assert entitlements.effective_entitlements(user).device_limit == 8
    assert UserPackage.objects.filter(user=user, is_baseline=False).count() == 1
