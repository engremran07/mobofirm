"""Model contracts of the small firmwares tables: uniqueness, ordering, cascades, labels.

Covers the changelog, firmware diff, verification and request-support tables, which have
no behaviour of their own but whose constraints other code relies on.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.firmwares import models as firmware_models
from apps.firmwares.models import (
    Brand,
    ChangelogEntry,
    DownloadToken,
    FirmwareDiff,
    HotlinkBlock,
    OfficialFirmware,
    ReleaseNote,
    TrustedTester,
    VerificationCredit,
    VerificationReport,
)
from apps.firmwares.tracking_models import FirmwareRequest, FirmwareRequestSupport

pytestmark = pytest.mark.django_db


def make_firmware(name: str) -> OfficialFirmware:
    return OfficialFirmware.objects.create(
        original_file_name=name, stored_file_path=f"/files/{name}", is_verified=True
    )


class TestChangelog:
    def test_versions_are_unique_and_listed_newest_first(self):
        old = ChangelogEntry.objects.create(
            version="1.0", title="First", release_date=date(2026, 1, 1)
        )
        new = ChangelogEntry.objects.create(
            version="1.1", title="Second", release_date=date(2026, 2, 1)
        )

        assert list(ChangelogEntry.objects.all()) == [new, old]
        assert str(new) == "v1.1: Second"
        with pytest.raises(IntegrityError), transaction.atomic():
            ChangelogEntry.objects.create(
                version="1.0", title="Again", release_date=date(2026, 3, 1)
            )

    def test_notes_belong_to_their_entry_and_go_with_it(self):
        entry = ChangelogEntry.objects.create(
            version="2.0", title="Notes", release_date=date(2026, 4, 1)
        )
        note = ReleaseNote.objects.create(
            entry=entry, category=ReleaseNote.Category.SECURITY, description="Patched"
        )

        assert str(note) == "[security] Patched"
        entry.delete()
        assert not ReleaseNote.objects.filter(pk=note.pk).exists()


class TestFirmwareDiff:
    def test_a_pair_has_one_diff_and_it_goes_with_either_file(self):
        old, new = make_firmware("old.zip"), make_firmware("new.zip")
        diff = FirmwareDiff.objects.create(old_firmware=old, new_firmware=new)

        assert str(diff).startswith("Diff FW#")
        with pytest.raises(IntegrityError), transaction.atomic():
            FirmwareDiff.objects.create(old_firmware=old, new_firmware=new)
        old.delete()
        assert not FirmwareDiff.objects.filter(pk=diff.pk).exists()


class TestVerification:
    def test_a_report_and_its_results_and_credits_follow_the_firmware(self, make_user):
        user = make_user("tester")
        tester = TrustedTester.objects.create(user=user)
        firmware = make_firmware("verified.zip")
        report = VerificationReport.objects.create(
            firmware=firmware, tester=tester, verdict=VerificationReport.Verdict.PASS
        )
        sub_test = firmware_models.TestResult.objects.create(
            report=report,
            test_name="boot",
            result=firmware_models.TestResult.Result.PASS,
        )
        credit = VerificationCredit.objects.create(
            user=user, firmware=firmware, report=report, credits_earned=Decimal("1.5")
        )

        assert report.status == VerificationReport.Status.SUBMITTED
        assert str(sub_test) == "boot: pass"
        assert f"FW#{firmware.pk}" in str(credit)
        firmware.delete()
        assert not VerificationReport.objects.filter(pk=report.pk).exists()
        assert not firmware_models.TestResult.objects.filter(pk=sub_test.pk).exists()
        assert not VerificationCredit.objects.filter(pk=credit.pk).exists()

    def test_a_user_is_one_tester(self, make_user):
        user = make_user("only-one")
        TrustedTester.objects.create(user=user)

        with pytest.raises(IntegrityError), transaction.atomic():
            TrustedTester.objects.create(user=user)

    def test_deleting_the_report_keeps_the_credit_record(self, make_user):
        user = make_user("credited")
        tester = TrustedTester.objects.create(user=user)
        firmware = make_firmware("credit.zip")
        report = VerificationReport.objects.create(
            firmware=firmware, tester=tester, verdict="pass"
        )
        credit = VerificationCredit.objects.create(
            user=user, firmware=firmware, report=report
        )

        report.delete()

        credit.refresh_from_db()
        assert credit.report is None


class TestRequestSupport:
    def test_a_user_supports_a_request_once(self, make_user, catalog):
        user = make_user("supporter")
        request = FirmwareRequest.objects.create(
            brand=catalog.brand, requested_by=user, firmware_type="official"
        )
        FirmwareRequestSupport.objects.create(request=request, user=user)

        with pytest.raises(IntegrityError), transaction.atomic():
            FirmwareRequestSupport.objects.create(request=request, user=user)

    def test_support_goes_with_its_request(self, make_user, catalog):
        user = make_user("supporter-two")
        request = FirmwareRequest.objects.create(
            brand=catalog.brand, requested_by=user, firmware_type="official"
        )
        support = FirmwareRequestSupport.objects.create(request=request, user=user)

        request.delete()

        assert not FirmwareRequestSupport.objects.filter(pk=support.pk).exists()


class TestDownloadTables:
    def test_a_token_value_is_unique(self):
        firmware = make_firmware("tokens.zip")
        expires = timezone.now() + timedelta(minutes=5)
        DownloadToken.objects.create(
            firmware=firmware, token="same", expires_at=expires
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            DownloadToken.objects.create(
                firmware=firmware, token="same", expires_at=expires
            )

    def test_a_blocked_domain_is_listed_once(self):
        HotlinkBlock.objects.create(domain="leech.example")

        with pytest.raises(IntegrityError), transaction.atomic():
            HotlinkBlock.objects.create(domain="leech.example")

    def test_a_brand_delete_leaves_firmware_unassigned_not_deleted(self):
        brand = Brand.objects.create(name="Gone", slug="gone")
        firmware = OfficialFirmware.objects.create(
            original_file_name="kept.zip", stored_file_path="/f/kept.zip", brand=brand
        )

        brand.delete()

        firmware.refresh_from_db()
        assert firmware.brand is None
