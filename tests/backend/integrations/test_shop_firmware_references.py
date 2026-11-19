"""Shop <-> firmwares: the shop cleans its file references when a firmware is deleted.

The shop attaches its purge to the firmware models by label (it never imports the
firmware app), so the list it keeps must stay equal to the firmware app's own sender
list, and every typed model must really reach the purge.
"""

from __future__ import annotations

import uuid

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete

from apps.firmwares.signal_handlers import FIRMWARE_SENDERS
from apps.shop import file_references
from apps.shop.models import QuotaConsumption

pytestmark = pytest.mark.django_db


def test_the_shop_listens_to_exactly_the_firmware_models() -> None:
    assert file_references.FIRMWARE_MODEL_LABELS == FIRMWARE_SENDERS


@pytest.mark.parametrize("label", FIRMWARE_SENDERS)
def test_deleting_any_typed_firmware_purges_the_shop_rows(
    label: str, django_user_model
) -> None:
    model = apps.get_model(label)
    user = django_user_model.objects.create_user(
        username="purge_user", email="purge_user@example.com", password="Pass-12345!"
    )
    ghost = model(pk=uuid.uuid4())
    content_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
    QuotaConsumption.objects.create(
        user=user, content_type=content_type, object_id=ghost.pk, reason="free"
    )
    kept = QuotaConsumption.objects.create(
        user=user,
        content_type=content_type,
        object_id=uuid.uuid4(),
        reason="free",
    )

    uid = f"{file_references.RECEIVER_UID}:{label}"
    assert (uid, id(model)) in [entry[0] for entry in post_delete.receivers], label
    file_references._on_firmware_deleted(model, ghost)

    assert list(QuotaConsumption.objects.values_list("pk", flat=True)) == [kept.pk]
