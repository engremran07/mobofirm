"""One typed firmware reference: every table that points at a firmware row cleans up.

F-019: firmware lives in six tables, so other tables store a typed reference
(``apps.firmwares.references``). Deleting a firmware of any type must leave no live row
behind; the append-only access history stays, with the file name it carried.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db.models import ProtectedError
from django.utils import timezone

from apps.firmwares.access_change_models import (
    AccessActor,
    AccessChangeLog,
    AccessChangeRequest,
    AccessChangeSource,
)
from apps.firmwares.catalog import PUBLIC_FIRMWARE_TYPES, TYPE_KEY_BY_CLASS
from apps.firmwares.firmware_cleanup import purge_firmware_references
from apps.firmwares.models import (
    AccessTier,
    AdGateLog,
    DownloadSession,
    DownloadToken,
    FlashingTool,
    FlashingToolCategory,
    ServedDownload,
)
from apps.firmwares.references import (
    content_type_for,
    ref_kwargs,
    type_key_for,
)
from apps.firmwares.shortener_models import (
    DownloadShortLink,
    FirmwareShortenerOverride,
    ShortenerOutcome,
)
from apps.firmwares.tracking_models import (
    FirmwareDownloadAttempt,
    FirmwareStats,
    FirmwareView,
)

TYPE_PARAMS = [pytest.param(key, cls, id=key) for key, cls in PUBLIC_FIRMWARE_TYPES]


def make(cls, catalog, name):
    return cls.objects.create(
        original_file_name=name,
        stored_file_path=f"/files/{name}",
        brand=catalog.brand,
        model=catalog.model,
        is_verified=True,
    )


def attach_everything(firmware, key, user):
    """One row in every table that references a firmware."""
    token = DownloadToken.objects.create(
        firmware=firmware, user=user, expires_at=timezone.now() + timedelta(hours=1)
    )
    session = DownloadSession.objects.create(token=token, user=user)
    AdGateLog.objects.create(token=token, session=session)
    FirmwareDownloadAttempt.objects.create(firmware=firmware, session=session)
    FirmwareView.objects.create(firmware=firmware, session_key="s")
    FirmwareStats.objects.create(firmware=firmware, date=timezone.now().date())
    ServedDownload.objects.create(user=user, firmware=firmware)
    AccessChangeRequest.objects.create(
        firmware=firmware,
        firmware_label=firmware.original_file_name,
        from_tier=AccessTier.FREE,
        to_tier=AccessTier.FEATURED,
        reason="popular",
    )
    AccessChangeLog.objects.create(
        firmware=firmware,
        firmware_label=firmware.original_file_name,
        actor_kind=AccessActor.STAFF,
        source=AccessChangeSource.ADMIN,
        from_tier=AccessTier.FREE,
        to_tier=AccessTier.FEATURED,
    )
    DownloadShortLink.objects.create(
        code=firmware.pk.hex[:8],
        firmware_type=key,
        firmware_id=firmware.pk,
        target_digest="d" * 64,
    )
    FirmwareShortenerOverride.objects.create(firmware_type=key, firmware_id=firmware.pk)
    ShortenerOutcome.objects.create(
        stage="create", result="ok", firmware_type=key, firmware_id=firmware.pk
    )


@pytest.mark.django_db
class TestTypedReference:
    def test_the_key_rule_matches_the_catalogue_type_table(self):
        assert {cls: type_key_for(cls) for _key, cls in PUBLIC_FIRMWARE_TYPES} == (
            TYPE_KEY_BY_CLASS
        )

    @pytest.mark.parametrize(("key", "cls"), TYPE_PARAMS)
    def test_the_reference_is_the_concrete_table_and_the_catalogue_key(
        self, catalog, key, cls
    ):
        firmware = make(cls, catalog, f"{key}.zip")

        assert type_key_for(firmware) == key
        assert type_key_for(cls) == key
        assert content_type_for(firmware) == ContentType.objects.get_for_model(
            cls, for_concrete_model=False
        )
        assert ref_kwargs(firmware) == {
            "content_type": content_type_for(cls),
            "object_id": firmware.pk,
        }

    @pytest.mark.parametrize(("key", "cls"), TYPE_PARAMS)
    def test_a_token_resolves_back_to_the_same_row(self, catalog, key, cls):
        firmware = make(cls, catalog, f"{key}-token.zip")
        token = DownloadToken.objects.create(
            firmware=firmware, expires_at=timezone.now() + timedelta(hours=1)
        )

        assert token.firmware == firmware
        assert list(firmware.download_tokens) == [token]


@pytest.mark.django_db
class TestDeletingFirmware:
    @pytest.mark.parametrize(("key", "cls"), TYPE_PARAMS)
    def test_no_live_row_survives_the_delete_and_history_is_kept(
        self, catalog, make_user, key, cls
    ):
        user = make_user(f"u-{key}")
        firmware = make(cls, catalog, f"{key}-gone.zip")
        bystander = make(cls, catalog, f"{key}-stays.zip")
        attach_everything(firmware, key, user)
        attach_everything(bystander, key, user)

        firmware_pk = firmware.pk
        firmware.delete()

        ref = {"object_id": firmware_pk}
        assert not DownloadToken.objects.filter(**ref).exists()
        assert not DownloadSession.objects.filter(token__object_id=firmware_pk).exists()
        assert not AdGateLog.objects.filter(token__object_id=firmware_pk).exists()
        assert not FirmwareDownloadAttempt.objects.filter(**ref).exists()
        assert not FirmwareView.objects.filter(**ref).exists()
        assert not FirmwareStats.objects.filter(**ref).exists()
        assert not ServedDownload.objects.filter(**ref).exists()
        assert not DownloadShortLink.objects.filter(firmware_id=firmware_pk).exists()
        assert not FirmwareShortenerOverride.objects.filter(
            firmware_id=firmware_pk
        ).exists()
        # Pending access requests are withdrawn, never left open for a missing file.
        request = AccessChangeRequest.objects.get(object_id=firmware_pk)
        assert request.status == AccessChangeRequest.Status.WITHDRAWN
        # Append-only history and audit rows survive, with the file name they carried.
        log = AccessChangeLog.objects.get(object_id=firmware_pk)
        assert log.firmware_label == f"{key}-gone.zip"
        assert ShortenerOutcome.objects.filter(firmware_id=firmware_pk).exists()
        # A different file of the same type is untouched.
        assert DownloadToken.objects.filter(object_id=bystander.pk).exists()
        assert ServedDownload.objects.filter(object_id=bystander.pk).exists()
        assert DownloadShortLink.objects.filter(firmware_id=bystander.pk).exists()
        assert AccessChangeRequest.objects.get(object_id=bystander.pk).is_pending

    def test_purging_twice_is_a_no_op(self, catalog, make_user):
        user = make_user("twice")
        firmware = make(PUBLIC_FIRMWARE_TYPES[0][1], catalog, "twice.zip")
        attach_everything(firmware, "official", user)

        first = purge_firmware_references(firmware)
        again = purge_firmware_references(firmware)

        assert first["tokens"] and first["short_links"]
        assert set(again.values()) == {0}

    def test_ids_of_other_types_are_not_confused(self, catalog, make_user):
        user = make_user("types")
        official = make(PUBLIC_FIRMWARE_TYPES[0][1], catalog, "off.zip")
        engineering = make(PUBLIC_FIRMWARE_TYPES[1][1], catalog, "eng.zip")
        attach_everything(official, "official", user)
        attach_everything(engineering, "engineering", user)
        engineering_pk = engineering.pk

        official.delete()

        assert DownloadToken.objects.filter(object_id=engineering_pk).exists()
        assert (
            DownloadShortLink.objects.filter(firmware_type="engineering").count() == 1
        )


@pytest.mark.django_db
class TestProtectedHistoryAndTools:
    def test_access_history_blocks_a_content_type_cleanup(self, catalog):
        firmware = make(PUBLIC_FIRMWARE_TYPES[0][1], catalog, "history.zip")
        AccessChangeLog.objects.create(
            firmware=firmware,
            firmware_label="history.zip",
            actor_kind=AccessActor.STAFF,
            source=AccessChangeSource.ADMIN,
            from_tier=AccessTier.FREE,
            to_tier=AccessTier.FEATURED,
        )

        with pytest.raises(ProtectedError):
            content_type_for(firmware).delete()

    def test_deleting_a_category_never_deletes_its_tools(self):
        category = FlashingToolCategory.objects.create(name="Keep", slug="keep")
        FlashingTool.objects.create(name="Tool", slug="tool", category=category)

        with pytest.raises(ProtectedError):
            category.delete()

        assert FlashingTool.objects.filter(slug="tool").exists()
