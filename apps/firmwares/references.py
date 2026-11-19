"""The single typed reference to a firmware row (F-019).

Firmware lives in six concrete tables (official, engineering, readback, modified,
other, unclassified) that share no primary-key space, so a database foreign key cannot
point at "a firmware". Other tables therefore store a *typed reference*: the table
(its catalogue key, e.g. ``official``) plus the row's UUID. This module is the only
place that knows how that pair is stored and read, so no other code builds it by hand:

* ``content_type`` + ``object_id`` columns (download tokens, served downloads, views,
  download attempts, daily stats, access-change requests and history) hold the table
  as a ``ContentType`` of the *concrete* model (never a proxy or the base class);
* ``firmware_type`` + ``firmware_id`` columns (the link shortener) hold the catalogue
  key directly.

Both encodings are derived from the same object by :func:`content_type_for` and
:func:`type_key_for`, and both are cleaned by
:func:`apps.firmwares.firmware_cleanup.purge_firmware_references` when a firmware row
is deleted, so a deleted file leaves no live row behind. Append-only history
(:class:`AccessChangeLog`, audit outcomes) is kept on purpose and carries the file name
it had.

Optional cross-app references (tags, SEO, comments, storage, shop) are those apps' own
generic relations; they must clean up on the delete signal this app already sends.
"""

from __future__ import annotations

from typing import Any

from django.contrib.contenttypes.models import ContentType

KEY_SUFFIX = "Firmware"


def content_type_for(target: Any) -> ContentType:
    """The ``ContentType`` of a firmware instance or class (never its parent)."""
    return ContentType.objects.get_for_model(target, for_concrete_model=False)


def ref_kwargs(firmware: Any) -> dict[str, Any]:
    """Lookup/creation kwargs for the ``content_type``/``object_id`` encoding."""
    return {"content_type": content_type_for(firmware), "object_id": firmware.pk}


def type_key_for(target: Any) -> str:
    """The catalogue key (``official`` ...) of a firmware instance or class.

    The six concrete classes are named ``<Key>Firmware`` (``OfficialFirmware``,
    ``UnclassifiedFirmware``, ...); a test checks the rule against the catalogue.
    """
    model_class = target if isinstance(target, type) else type(target)
    return model_class.__name__.removesuffix(KEY_SUFFIX).lower()
