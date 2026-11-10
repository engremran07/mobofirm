"""Popularity of public firmware and device models from real analytics.

A file's score is ``DOWNLOAD_WEIGHT`` times its completed downloads plus its page
views inside the window. Only active, staff-approved files are counted (the scoring
queries are restricted to the public catalogue queryset), so unapproved or hidden
firmware can never surface. Each call costs two grouped queries per classification
plus one fetch of the winners; callers cache the result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from .catalog import PUBLIC_FIRMWARE_TYPES, get_public_firmware_queryset
from .models import DownloadToken, Model
from .tracking_models import FirmwareView

DOWNLOAD_WEIGHT: Final[int] = 5
SCORE_POOL: Final[int] = 200


@dataclass(frozen=True, slots=True)
class PopularFile:
    """One firmware file with its popularity score and classification key."""

    type_key: str
    firmware: Any
    score: int


def _scoped(queryset: QuerySet[Any], brand: str, chipset: str) -> QuerySet[Any]:
    if brand:
        queryset = queryset.filter(brand__slug=brand)
    if chipset:
        queryset = queryset.filter(
            Q(model__chipset_ref__slug=chipset) | Q(variant__chipset_ref__slug=chipset)
        )
    return queryset


def _scores(
    content_type: ContentType, since: datetime, public: QuerySet[Any]
) -> dict[UUID, int]:
    ids = public.values("pk")
    scores: dict[UUID, int] = defaultdict(int)
    downloads = (
        DownloadToken.objects.filter(
            content_type=content_type,
            status=DownloadToken.Status.USED,
            used_at__gte=since,
            object_id__in=ids,
        )
        .order_by()
        .values("object_id")
        .annotate(total=Count("pk"))
    )
    for row in downloads:
        scores[row["object_id"]] += DOWNLOAD_WEIGHT * row["total"]
    views = (
        FirmwareView.objects.filter(
            content_type=content_type, viewed_at__gte=since, object_id__in=ids
        )
        .order_by()
        .values("object_id")
        .annotate(total=Count("pk"))
    )
    for row in views:
        scores[row["object_id"]] += row["total"]
    return scores


def _ranked_ids(
    window_days: int, brand: str, chipset: str, pool: int
) -> list[tuple[str, UUID, int]]:
    """``(type key, file id, score)`` of the best files across classifications."""
    since = timezone.now() - timedelta(days=window_days)
    ranked: list[tuple[str, UUID, int]] = []
    for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
        public = _scoped(get_public_firmware_queryset(model_class), brand, chipset)
        content_type = ContentType.objects.get_for_model(
            model_class, for_concrete_model=False
        )
        scores = _scores(content_type, since, public)
        ranked.extend((type_key, pk, score) for pk, score in scores.items() if score)
    ranked.sort(key=lambda item: (-item[2], str(item[1])))
    return ranked[:pool]


def popular_files(
    *, limit: int, window_days: int, brand: str = "", chipset: str = ""
) -> list[PopularFile]:
    """The most downloaded and viewed public files of the window, best first."""
    ranked = _ranked_ids(window_days, brand, chipset, limit)
    wanted: dict[str, list[UUID]] = defaultdict(list)
    for type_key, pk, _score in ranked:
        wanted[type_key].append(pk)
    objects: dict[tuple[str, UUID], Any] = {}
    for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
        if type_key not in wanted:
            continue
        rows = get_public_firmware_queryset(model_class).filter(pk__in=wanted[type_key])
        for firmware in rows.select_related("brand", "model", "variant"):
            objects[(type_key, firmware.pk)] = firmware
    return [
        PopularFile(type_key, objects[(type_key, pk)], score)
        for type_key, pk, score in ranked
        if (type_key, pk) in objects
    ]


def _model_totals(ranked: list[tuple[str, UUID, int]]) -> dict[int, int]:
    """Sum the file scores per device model."""
    wanted: dict[str, list[UUID]] = defaultdict(list)
    score_by_file: dict[tuple[str, UUID], int] = {}
    for type_key, pk, score in ranked:
        wanted[type_key].append(pk)
        score_by_file[(type_key, pk)] = score
    totals: dict[int, int] = defaultdict(int)
    for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
        if type_key not in wanted:
            continue
        pairs = (
            get_public_firmware_queryset(model_class)
            .filter(pk__in=wanted[type_key], model__isnull=False)
            .values_list("pk", "model_id")
        )
        for pk, model_id in pairs:
            totals[model_id] += score_by_file[(type_key, pk)]
    return totals


def popular_models(
    *, limit: int, window_days: int, brand: str = "", chipset: str = ""
) -> list[Model]:
    """Active device models whose files were downloaded and viewed most."""
    totals = _model_totals(_ranked_ids(window_days, brand, chipset, SCORE_POOL))
    order = sorted(totals, key=lambda model_id: (-totals[model_id], model_id))
    models = Model.objects.filter(pk__in=order, is_active=True).select_related("brand")
    by_id = {model.pk: model for model in models}
    return [by_id[model_id] for model_id in order if model_id in by_id][:limit]
